from app.services.regression_test_service import RegressionTestService


def test_map_github_conclusion_to_status():
    svc = RegressionTestService()
    assert svc._map_github_conclusion_to_status("success") == "PASSED"
    assert svc._map_github_conclusion_to_status("timed_out") == "TIMEOUT"
    assert svc._map_github_conclusion_to_status("neutral") == "SKIPPED"
    assert svc._map_github_conclusion_to_status("failure") == "FAILED"


def test_summarize_jobs_counts_outcomes():
    svc = RegressionTestService()
    summary = svc._summarize_jobs(
        [
            {"conclusion": "success"},
            {"conclusion": "failure"},
            {"conclusion": "timed_out"},
            {"conclusion": "neutral"},
            {"conclusion": "mystery"},
        ]
    )
    assert summary == {"passed": 1, "failed": 2, "errors": 1, "skipped": 1}


def test_run_uses_local_fallback_for_non_github_repo(monkeypatch):
    svc = RegressionTestService()
    monkeypatch.setattr(
        svc,
        "_run_local",
        lambda: {
            "status": "SKIPPED",
            "reason": "local fallback",
            "command": "python3 -m pytest -q tests",
            "summary": {"passed": 0, "failed": 0, "errors": 0, "skipped": 0},
            "durationSeconds": 0.0,
            "outputSnippet": "",
        },
    )
    result = svc.run(repo_url="https://gitlab.com/acme/repo", pr_number=10, github_token="token")
    assert result["command"] == "python3 -m pytest -q tests"
