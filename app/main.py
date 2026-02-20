import logging
import os
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import Depends, FastAPI, HTTPException
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
app.add_exception_handler(RateLimitExceeded, lambda request, exc: HTTPException(status_code=429, detail="Rate limit exceeded"))
app.add_middleware(SlowAPIMiddleware)


github_service = GitHubService()
parser = DependencyParser()
graph_engine = GraphEngine()
llm_agent = LLMAgent()
risk_engine = RiskEngine()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    if payload.username != "admin" or payload.password != "admin123":
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(payload.username)
    return LoginResponse(access_token=token)


@app.post("/analyze-pr", response_model=AnalysisResponse)
@limiter.limit(settings.rate_limit)
def analyze_pr(request, payload: AnalyzePRRequest, user: str = Depends(get_current_user)) -> AnalysisResponse:
    logger.info("Analyze PR request", extra={"user": user, "pr": payload.pr_number, "repo": str(payload.repo_url)})

    pr_data = github_service.fetch_pr_data(str(payload.repo_url), payload.pr_number)
    changed_files = [f["path"] for f in pr_data["changed_files"]]
    diff_blob = "\n".join([f.get("patch", "") or "" for f in pr_data["changed_files"]])

    node_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../sample-microservices-node"))
    dep_map, services = parser.parse_project(node_root)
    graph_engine.build_graph(dep_map, services)
    graph_result = graph_engine.analyze_impact(changed_files)

    llm_result = llm_agent.predict(diff_blob, graph_result, changed_files, pr_data["commit_messages"])

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
    }

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
