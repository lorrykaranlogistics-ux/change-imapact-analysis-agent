from fastapi.testclient import TestClient

from app.main import app
from app.services.service_errors import ServiceError
from app.utils.security import create_access_token


def _auth_headers():
    token = create_access_token("admin")
    return {"Authorization": f"Bearer {token}"}


class _DummyDB:
    def add(self, _obj):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class _DummySettingsDB(_DummyDB):
    def __init__(self):
        self.project_settings = None

    def get(self, model, key):
        if model.__name__ == "ProjectSettings" and key == 1:
            return self.project_settings
        return None

    def add(self, obj):
        if obj.__class__.__name__ == "ProjectSettings":
            self.project_settings = obj


def test_analyze_pr_returns_403_when_service_error(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: _DummyDB())

    def _raise(*args, **kwargs):
        raise ServiceError("access denied", status_code=403, code="github_access_denied")

    monkeypatch.setattr(main_mod.github_service, "fetch_pr_data", _raise)

    with TestClient(app) as client:
        res = client.post(
            "/analyze-pr",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={
                "repo_url": "https://github.com/acme/private-repo",
                "pr_number": 10,
                "use_llm": False,
            },
        )

    assert res.status_code == 403
    body = res.json()
    assert body["detail"]["code"] == "github_access_denied"


def test_analyze_pr_succeeds_without_llm(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: _DummyDB())

    monkeypatch.setattr(
        main_mod.github_service,
        "fetch_pr_data",
        lambda repo_url, pr_number, github_token=None: {
            "pr_number": pr_number,
            "title": "demo",
            "body": "demo",
            "commit_messages": ["feat: change"],
            "changed_files": [
                {
                    "path": "payment-service/src/controllers/paymentController.js",
                    "additions": 10,
                    "deletions": 2,
                    "patch": "+validate\\n-if (x)",
                }
            ],
        },
    )
    monkeypatch.setattr(main_mod.parser, "parse_project", lambda _root: ({}, ["payment-service"]))
    monkeypatch.setattr(main_mod.graph_engine, "build_graph", lambda dep_map, services: None)
    monkeypatch.setattr(
        main_mod.graph_engine,
        "analyze_impact",
        lambda changed_files: {
            "impacted_services": ["payment-service", "order-service"],
            "dependency_depth": 2,
            "upstream_dependencies": [],
            "downstream_dependencies": [],
            "cross_service_impacts": {},
        },
    )
    monkeypatch.setattr(
        main_mod.llm_agent,
        "predict_heuristic",
        lambda pr_diff, graph_result, changed_files, commit_messages: {
            "classification": {
                "breakingChange": False,
                "schemaChange": True,
                "logicChange": True,
                "configChange": False,
            },
            "riskLevel": "MEDIUM",
            "regressionAreas": ["payment flow"],
            "suggestedTests": ["payment validation test"],
        },
    )

    with TestClient(app) as client:
        res = client.post(
            "/analyze-pr",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={
                "repo_url": "https://github.com/acme/public-repo",
                "pr_number": 1,
                "use_llm": False,
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body["prNumber"] == 1
    assert body["riskLevel"] in {"LOW", "MEDIUM", "HIGH"}
    assert "payment-service" in body["impactedServices"]
    assert "sanityCheckResults" in body
    assert body["sanityCheckResults"]["status"] == "PASSED"
    assert "regressionTestResults" in body
    assert body["regressionTestResults"]["status"] == "SKIPPED"


def test_project_settings_upsert_and_get(monkeypatch):
    import app.main as main_mod

    settings_db = _DummySettingsDB()
    monkeypatch.setattr(main_mod.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: settings_db)

    with TestClient(app) as client:
        put_res = client.put(
            "/project-settings",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={"project_name": "Acme Platform", "github_token": "ghp_test123"},
        )
        get_res = client.get("/project-settings", headers=_auth_headers())

    assert put_res.status_code == 200
    assert put_res.json() == {"project_name": "Acme Platform", "github_token": "ghp_test123"}
    assert get_res.status_code == 200
    assert get_res.json() == {"project_name": "Acme Platform", "github_token": "ghp_test123"}


def test_project_settings_get_returns_empty_when_not_saved(monkeypatch):
    import app.main as main_mod

    settings_db = _DummySettingsDB()
    monkeypatch.setattr(main_mod.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: settings_db)

    with TestClient(app) as client:
        res = client.get("/project-settings", headers=_auth_headers())

    assert res.status_code == 200
    assert res.json() == {"project_name": "", "github_token": None}


def test_analyze_pr_passes_repo_context_to_regression_runner(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(
        main_mod.github_service,
        "fetch_pr_data",
        lambda repo_url, pr_number, github_token=None: {
            "pr_number": pr_number,
            "title": "demo",
            "body": "demo",
            "commit_messages": ["feat: change"],
            "changed_files": [
                {
                    "path": "payment-service/src/controllers/paymentController.js",
                    "additions": 10,
                    "deletions": 2,
                    "patch": "+validate\\n-if (x)",
                }
            ],
        },
    )
    monkeypatch.setattr(main_mod.parser, "parse_project", lambda _root: ({}, ["payment-service"]))
    monkeypatch.setattr(main_mod.graph_engine, "build_graph", lambda dep_map, services: None)
    monkeypatch.setattr(
        main_mod.graph_engine,
        "analyze_impact",
        lambda changed_files: {
            "impacted_services": ["payment-service"],
            "dependency_depth": 1,
            "upstream_dependencies": [],
            "downstream_dependencies": [],
            "cross_service_impacts": {},
        },
    )
    monkeypatch.setattr(
        main_mod.llm_agent,
        "predict_heuristic",
        lambda pr_diff, graph_result, changed_files, commit_messages: {
            "classification": {
                "breakingChange": False,
                "schemaChange": False,
                "logicChange": True,
                "configChange": False,
            },
            "riskLevel": "LOW",
            "regressionAreas": ["payment flow"],
            "suggestedTests": ["payment validation test"],
        },
    )

    observed = {}

    def _fake_run(repo_url=None, pr_number=None, github_token=None):
        observed["repo_url"] = repo_url
        observed["pr_number"] = pr_number
        observed["github_token"] = github_token
        return {
            "status": "PASSED",
            "command": "github-actions:acme/repo",
            "summary": {"passed": 1, "failed": 0, "errors": 0, "skipped": 0},
            "durationSeconds": 1.23,
            "outputSnippet": "ok",
        }

    monkeypatch.setattr(main_mod.regression_test_service, "run", _fake_run)

    with TestClient(app) as client:
        res = client.post(
            "/analyze-pr",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={
                "repo_url": "https://github.com/acme/private-repo",
                "pr_number": 7,
                "use_llm": False,
                "run_regression_tests": True,
                "github_token": "ghp_abc123",
            },
        )

    assert res.status_code == 200
    assert observed == {
        "repo_url": "https://github.com/acme/private-repo",
        "pr_number": 7,
        "github_token": "ghp_abc123",
    }


def test_analyze_pr_increases_risk_when_regression_fails(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod.Base.metadata, "create_all", lambda bind: None)
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(
        main_mod.github_service,
        "fetch_pr_data",
        lambda repo_url, pr_number, github_token=None: {
            "pr_number": pr_number,
            "title": "demo",
            "body": "demo",
            "commit_messages": ["feat: change"],
            "changed_files": [
                {
                    "path": "payment-service/src/controllers/paymentController.js",
                    "additions": 10,
                    "deletions": 2,
                    "patch": "+validate\\n-if (x)",
                }
            ],
        },
    )
    monkeypatch.setattr(main_mod.parser, "parse_project", lambda _root: ({}, ["payment-service"]))
    monkeypatch.setattr(main_mod.graph_engine, "build_graph", lambda dep_map, services: None)
    monkeypatch.setattr(
        main_mod.graph_engine,
        "analyze_impact",
        lambda changed_files: {
            "impacted_services": ["payment-service"],
            "dependency_depth": 0,
            "upstream_dependencies": [],
            "downstream_dependencies": [],
            "cross_service_impacts": {},
        },
    )
    monkeypatch.setattr(
        main_mod.llm_agent,
        "predict_heuristic",
        lambda pr_diff, graph_result, changed_files, commit_messages: {
            "classification": {
                "breakingChange": False,
                "schemaChange": False,
                "logicChange": True,
                "configChange": False,
            },
            "riskLevel": "LOW",
            "regressionAreas": ["payment flow"],
            "suggestedTests": ["payment validation test"],
        },
    )
    monkeypatch.setattr(
        main_mod.regression_test_service,
        "run",
        lambda repo_url=None, pr_number=None, github_token=None: {
            "status": "FAILED",
            "command": "github-actions:acme/repo",
            "summary": {"passed": 0, "failed": 1, "errors": 0, "skipped": 0},
            "durationSeconds": 3.0,
            "outputSnippet": "failed",
        },
    )

    with TestClient(app) as client:
        res = client.post(
            "/analyze-pr",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json={
                "repo_url": "https://github.com/acme/private-repo",
                "pr_number": 8,
                "use_llm": False,
                "run_regression_tests": True,
                "github_token": "ghp_abc123",
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body["regressionTestResults"]["status"] == "FAILED"
    assert body["riskScore"] >= 20
    assert body["riskLevel"] in {"LOW", "MEDIUM", "HIGH"}
