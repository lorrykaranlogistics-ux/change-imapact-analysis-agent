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
