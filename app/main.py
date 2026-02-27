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
from app.db.models import AnalysisHistory
from app.models.schemas import AnalyzePRRequest, AnalysisResponse, LoginRequest, LoginResponse
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


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    if payload.username != "admin" or payload.password != "admin123":
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(payload.username)
    return LoginResponse(access_token=token)


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
    diff_blob = "\n".join([f.get("patch", "") or "" for f in changed_file_items])

    node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../sample-microservices-node"))
    dep_map, services = parser.parse_project(node_root)
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
        "changedFiles": changed_files,
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
        result["regressionTestResults"] = regression_test_service.run()

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
