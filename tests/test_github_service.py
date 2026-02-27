from app.config import settings
from app.services.github_service import GitHubService


def test_build_clone_url_uses_request_token_and_encodes():
    svc = GitHubService()
    url = svc._build_clone_url("acme", "private-repo", "ghp_tok/en+space")
    assert "x-access-token:" in url
    assert "ghp_tok%2Fen%2Bspace" in url
    assert url.endswith("@github.com/acme/private-repo.git")


def test_build_headers_uses_request_token_over_env():
    svc = GitHubService()
    original = settings.github_token
    settings.github_token = "env_token"
    try:
        headers = svc._build_headers("req_token")
        assert headers["Authorization"] == "Bearer req_token"
    finally:
        settings.github_token = original


def test_build_headers_without_token_has_no_auth_header():
    svc = GitHubService()
    original = settings.github_token
    settings.github_token = ""
    try:
        headers = svc._build_headers(None)
        assert "Authorization" not in headers
    finally:
        settings.github_token = original
