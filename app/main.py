import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy.exc import SQLAlchemyError

from app.agents.llm_agent import LLMAgent
from app.agents.risk_engine import RiskEngine
from app.config import settings
from app.db.database import Base, SessionLocal, engine
from app.db.models import AnalysisHistory, ProjectSettings
from app.models.schemas import (
    AnalyzePRRequest,
    AnalysisResponse,
    LoginRequest,
    LoginResponse,
    ProjectSettingsResponse,
    ProjectSettingsUpsertRequest,
)
from app.services.dependency_parser import DependencyParser
from app.services.github_service import GitHubService
from app.services.graph_engine import GraphEngine
from app.services.regression_test_service import RegressionTestService
from app.services.service_errors import ServiceError
from app.utils.logging_utils import configure_logging
from app.utils.security import create_access_token, get_current_user

configure_logging()
logger = logging.getLogger("impact-agent")

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Enterprise DevOps Change Impact Analysis Agent", lifespan=lifespan)
app.state.limiter = limiter
allowed_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(
    RateLimitExceeded,
    lambda request, exc: JSONResponse(status_code=429, content={"detail": "Rate limit exceeded", "code": "rate_limited"}),
)
app.add_middleware(SlowAPIMiddleware)


github_service = GitHubService()
parser = DependencyParser()
graph_engine = GraphEngine()
llm_agent = LLMAgent()
risk_engine = RiskEngine()
regression_test_service = RegressionTestService()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _sanity_check_pr_data(pr_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    changed_files = pr_data.get("changed_files")
    if not isinstance(changed_files, list):
        raise HTTPException(
            status_code=502,
            detail={"message": "Invalid PR payload: changed_files is missing", "code": "invalid_pr_payload"},
        )
    if not changed_files:
        raise HTTPException(
            status_code=422,
            detail={"message": "PR has no changed files to analyze", "code": "no_changed_files"},
        )
    for idx, file_obj in enumerate(changed_files):
        path = file_obj.get("path") if isinstance(file_obj, dict) else None
        if not path:
            raise HTTPException(
                status_code=502,
                detail={"message": f"Invalid PR payload: missing file path at index {idx}", "code": "invalid_pr_payload"},
            )
    return changed_files


def _sanity_check_graph_result(graph_result: Dict[str, Any]) -> None:
    if "impacted_services" not in graph_result or "dependency_depth" not in graph_result:
        raise HTTPException(
            status_code=502,
            detail={"message": "Invalid graph analysis result", "code": "invalid_graph_result"},
        )


def _risk_level_rank(level: str) -> int:
    mapping = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    return mapping.get(level.upper(), 1)


def _risk_level_from_score(score: int) -> str:
    if score >= 75:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _apply_regression_signal(
    risk_score: int,
    risk_level: str,
    regression_result: Dict[str, Any],
) -> Dict[str, Any]:
    status = str(regression_result.get("status", "SKIPPED")).upper()
    penalty_by_status = {
        "PASSED": 0,
        "SKIPPED": 0,
        "FAILED": 20,
        "TIMEOUT": 25,
    }
    penalty = penalty_by_status.get(status, 10)
    adjusted_score = max(1, min(100, risk_score + penalty))
    score_based_level = _risk_level_from_score(adjusted_score)
    adjusted_level = score_based_level
    if _risk_level_rank(score_based_level) < _risk_level_rank(risk_level):
        adjusted_level = risk_level
    return {"riskScore": adjusted_score, "riskLevel": adjusted_level}


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    if payload.username != "admin" or payload.password != "admin123":
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(payload.username)
    return LoginResponse(access_token=token)


@app.get("/project-settings", response_model=ProjectSettingsResponse)
def get_project_settings(user: str = Depends(get_current_user)) -> ProjectSettingsResponse:
    db = SessionLocal()
    try:
        settings_row = db.get(ProjectSettings, 1)
        if not settings_row:
            return ProjectSettingsResponse(project_name="", github_token=None)
        return ProjectSettingsResponse(
            project_name=settings_row.project_name,
            github_token=settings_row.github_token,
        )
    finally:
        db.close()


@app.put("/project-settings", response_model=ProjectSettingsResponse)
def upsert_project_settings(
    payload: ProjectSettingsUpsertRequest, user: str = Depends(get_current_user)
) -> ProjectSettingsResponse:
    project_name = payload.project_name.strip()
    github_token = payload.github_token.strip() if payload.github_token else None
    if github_token == "":
        github_token = None

    db = SessionLocal()
    try:
        settings_row = db.get(ProjectSettings, 1)
        if not settings_row:
            settings_row = ProjectSettings(id=1, project_name=project_name, github_token=github_token)
            db.add(settings_row)
        else:
            settings_row.project_name = project_name
            settings_row.github_token = github_token
        db.commit()
        return ProjectSettingsResponse(
            project_name=settings_row.project_name,
            github_token=settings_row.github_token,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to persist project settings", extra={"error": str(exc)})
        raise HTTPException(
            status_code=500,
            detail={"message": "Failed to store project settings", "code": "db_write_failed"},
        ) from exc
    finally:
        db.close()


@app.post("/analyze-pr", response_model=AnalysisResponse)
@limiter.limit(settings.rate_limit)
def analyze_pr(request: Request, payload: AnalyzePRRequest, user: str = Depends(get_current_user)) -> AnalysisResponse:
    logger.info("Analyze PR request", extra={"user": user, "pr": payload.pr_number, "repo": str(payload.repo_url)})

    try:
        pr_data = github_service.fetch_pr_data(str(payload.repo_url), payload.pr_number, payload.github_token)
    except ServiceError as exc:
        logger.warning("PR data fetch failed", extra={"error": exc.message, "code": exc.code})
        raise HTTPException(status_code=exc.status_code, detail={"message": exc.message, "code": exc.code}) from exc
    changed_file_items = _sanity_check_pr_data(pr_data)
    changed_files = [f["path"] for f in changed_file_items]
    changed_files_report = [
        {
            "filename": f["path"],
            "status": "modified",
            "additions": f.get("additions", 0),
            "deletions": f.get("deletions", 0),
        }
        for f in changed_file_items
    ]
    diff_blob = "\n".join([f.get("patch", "") or "" for f in changed_file_items])

    configured_root = settings.microservices_project_path.strip()
    default_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../microservices-project"))
    node_root = configured_root or default_root

    dep_map: Dict[str, List[str]] = {}
    services: set[str] = set()
    if os.path.isdir(node_root):
        try:
            dep_map, services = parser.parse_project(node_root)
        except Exception as exc:
            logger.warning("Dependency parser failed; continuing with empty graph", extra={"error": str(exc)})
    else:
        logger.info("Microservices project path not found; continuing with empty graph", extra={"path": node_root})

    graph_engine.build_graph(dep_map, services)
    graph_result = graph_engine.analyze_impact(changed_files)
    _sanity_check_graph_result(graph_result)
    sanity_results = {
        "status": "PASSED",
        "checks": [
            {"name": "pr_payload_changed_files", "status": "PASSED", "count": len(changed_file_items)},
            {
                "name": "graph_result_shape",
                "status": "PASSED",
                "impactedServicesCount": len(graph_result.get("impacted_services", [])),
                "dependencyDepth": graph_result.get("dependency_depth"),
            },
        ],
    }

    if payload.use_llm:
        llm_result = llm_agent.predict(diff_blob, graph_result, changed_files, pr_data["commit_messages"])
    else:
        llm_result = llm_agent.predict_heuristic(diff_blob, graph_result, changed_files, pr_data["commit_messages"])

    line_count = sum((f["additions"] + f["deletions"]) for f in pr_data["changed_files"])
    changed_services = set(path.split("/")[0] for path in changed_files)
    risk = risk_engine.score(
        lines_changed=line_count,
        dependency_depth=graph_result["dependency_depth"],
        changed_services=changed_services,
        llm_risk_level=llm_result["riskLevel"],
    )

    result = {
        "prNumber": payload.pr_number,
        "changedFiles": changed_files_report,
        "impactedServices": graph_result["impacted_services"],
        "regressionAreas": llm_result["regressionAreas"],
        "suggestedTests": llm_result["suggestedTests"],
        "riskScore": risk["riskScore"],
        "riskLevel": risk["riskLevel"],
        "confidence": risk["confidence"],
        "changeClassification": llm_result["classification"],
        "dependencyDepth": graph_result["dependency_depth"],
        "sanityCheckResults": sanity_results,
        "regressionTestResults": regression_test_service.skipped("set run_regression_tests=true to execute"),
    }
    if payload.run_regression_tests:
        result["regressionTestResults"] = regression_test_service.run(
            repo_url=str(payload.repo_url),
            pr_number=payload.pr_number,
            github_token=payload.github_token,
        )
        adjusted = _apply_regression_signal(
            risk_score=result["riskScore"],
            risk_level=result["riskLevel"],
            regression_result=result["regressionTestResults"],
        )
        result["riskScore"] = adjusted["riskScore"]
        result["riskLevel"] = adjusted["riskLevel"]

    db = SessionLocal()
    try:
        db.add(AnalysisHistory(repo_url=str(payload.repo_url), pr_number=payload.pr_number, result=result))
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.error("Failed to persist analysis", extra={"error": str(exc)})
    finally:
        db.close()

    return AnalysisResponse(**result)
