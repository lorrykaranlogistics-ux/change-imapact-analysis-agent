from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=3, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AnalyzePRRequest(BaseModel):
    repo_url: HttpUrl
    pr_number: int = Field(gt=0)
    use_llm: bool = True
    run_regression_tests: bool = True
    github_token: Optional[str] = Field(default=None, min_length=1)


class ProjectSettingsUpsertRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=255)
    github_token: Optional[str] = Field(default=None, max_length=1024)


class ProjectSettingsResponse(BaseModel):
    project_name: str
    github_token: Optional[str] = None


class ChangedFile(BaseModel):
    path: str
    additions: int
    deletions: int
    patch: Optional[str] = None


class ChangedFileReportItem(BaseModel):
    filename: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0


class PRData(BaseModel):
    pr_number: int
    title: str
    body: str
    commit_messages: List[str]
    changed_files: List[ChangedFile]


class GraphResult(BaseModel):
    impacted_services: List[str]
    dependency_depth: int
    upstream_dependencies: List[str]
    downstream_dependencies: List[str]
    cross_service_impacts: Dict[str, List[str]]


class RiskScore(BaseModel):
    riskScore: int
    riskLevel: str
    confidence: float


class AnalysisResponse(BaseModel):
    prNumber: int
    changedFiles: List[ChangedFileReportItem]
    impactedServices: List[str]
    regressionAreas: List[str]
    suggestedTests: List[str]
    riskScore: int
    riskLevel: str
    confidence: float
    changeClassification: Dict[str, Any]
    dependencyDepth: int
    sanityCheckResults: Dict[str, Any]
    regressionTestResults: Dict[str, Any]
