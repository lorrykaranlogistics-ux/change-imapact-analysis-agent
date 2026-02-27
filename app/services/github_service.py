import logging
import os
import re
import tempfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from git import Repo

from app.config import settings
from app.services.service_errors import ServiceError

logger = logging.getLogger(__name__)


class GitHubService:
    def __init__(self) -> None:
        self.api_base_url = settings.github_api_base_url.rstrip("/")
        self.timeout = settings.github_api_timeout_seconds

    def _parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", repo_url)
        if not match:
            raise ServiceError("Invalid GitHub repository URL", status_code=400, code="invalid_repo_url")
        return match.group("owner"), match.group("repo")

    def fetch_pr_data(self, repo_url: str, pr_number: int, github_token: Optional[str] = None) -> Dict:
        owner, repo = self._parse_repo_url(repo_url)
        # Demo-first implementation: reads from local sample PR artifact for reproducibility.
        local_pr_patch = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../sample-microservices-node/sample-pr/pr-24.patch")
        )
        if pr_number == 24 and os.path.exists(local_pr_patch):
            return self._from_local_patch(local_pr_patch, pr_number)

        try:
            return self._fetch_pr_data_from_api(owner, repo, pr_number, github_token)
        except ServiceError as exc:
            logger.warning(
                "GitHub API PR fetch failed, trying git fallback",
                extra={"repo": f"https://github.com/{owner}/{repo}.git", "pr": pr_number, "error": exc.message},
            )
        except Exception as exc:
            logger.warning(
                "Unexpected API fetch error, trying git fallback",
                extra={"repo": f"https://github.com/{owner}/{repo}.git", "pr": pr_number, "error": str(exc)},
            )

        return self._fetch_pr_data_via_git(owner, repo, pr_number, github_token)

    def _fetch_pr_data_from_api(self, owner: str, repo: str, pr_number: int, github_token: Optional[str]) -> Dict:
        headers = self._build_headers(github_token)
        pr_url = f"{self.api_base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
        files_url = f"{pr_url}/files"
        commits_url = f"{pr_url}/commits"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                pr_resp = client.get(pr_url, headers=headers)
                self._raise_for_github_error(pr_resp, owner, repo, pr_number)
                pr_obj = pr_resp.json()

                changed_files = self._fetch_paginated_files(client, files_url, headers, owner, repo, pr_number)

                commits_resp = client.get(commits_url, headers=headers, params={"per_page": 10, "page": 1})
                self._raise_for_github_error(commits_resp, owner, repo, pr_number)
                commit_messages = [
                    c.get("commit", {}).get("message", "").strip()
                    for c in commits_resp.json()
                    if c.get("commit", {}).get("message")
                ]
        except httpx.RequestError as exc:
            raise ServiceError(
                "Unable to reach GitHub API while fetching PR data",
                status_code=502,
                code="github_api_unreachable",
            ) from exc

        return {
            "pr_number": pr_number,
            "title": pr_obj.get("title", f"PR #{pr_number}"),
            "body": pr_obj.get("body") or "",
            "commit_messages": commit_messages,
            "changed_files": changed_files,
        }

    def _fetch_paginated_files(
        self,
        client: httpx.Client,
        files_url: str,
        headers: Dict[str, str],
        owner: str,
        repo: str,
        pr_number: int,
    ) -> List[Dict]:
        changed_files: List[Dict] = []
        page = 1
        per_page = 100

        while True:
            response = client.get(files_url, headers=headers, params={"per_page": per_page, "page": page})
            if response.status_code >= 400:
                self._raise_for_github_error(response, owner, repo, pr_number)

            files = response.json()
            for f in files:
                changed_files.append(
                    {
                        "path": f.get("filename", ""),
                        "additions": int(f.get("additions", 0)),
                        "deletions": int(f.get("deletions", 0)),
                        "patch": f.get("patch"),
                    }
                )

            if len(files) < per_page:
                break
            page += 1

        return changed_files

    def _fetch_pr_data_via_git(self, owner: str, repo: str, pr_number: int, github_token: Optional[str]) -> Dict:
        # Fallback: clone and inspect branch refs like pull/<n>/head if available.
        with tempfile.TemporaryDirectory() as tmp:
            clone_url = self._build_clone_url(owner, repo, github_token)
            logger.info("Cloning repository", extra={"repo": f"https://github.com/{owner}/{repo}.git"})
            try:
                git_repo = Repo.clone_from(clone_url, tmp, depth=200)
            except Exception as exc:
                raise ServiceError(
                    "Unable to clone repository. For private repos, provide github_token in request or set GITHUB_TOKEN.",
                    status_code=400,
                    code="github_clone_failed",
                ) from exc
            target_ref = f"pull/{pr_number}/head"
            try:
                git_repo.git.fetch("origin", target_ref)
                git_repo.git.checkout("FETCH_HEAD")
            except Exception:
                raise ServiceError(
                    "Unable to fetch PR ref. Ensure PR exists and token has repository access.",
                    status_code=404,
                    code="pr_ref_unavailable",
                )

            changed_files: List[Dict] = []
            commit_messages = [c.message.strip() for c in git_repo.iter_commits("HEAD", max_count=10)]
            for diff in git_repo.head.commit.diff("HEAD~1", create_patch=True):
                patch = diff.diff.decode("utf-8", errors="ignore") if diff.diff else ""
                changed_files.append(
                    {
                        "path": diff.b_path or diff.a_path,
                        "additions": patch.count("\n+") - patch.count("\n+++"),
                        "deletions": patch.count("\n-") - patch.count("\n---"),
                        "patch": patch,
                    }
                )
            return {
                "pr_number": pr_number,
                "title": f"PR #{pr_number}",
                "body": "Fetched from git refs",
                "commit_messages": commit_messages,
                "changed_files": changed_files,
            }

    def _build_clone_url(self, owner: str, repo: str, request_token: Optional[str]) -> str:
        token = (request_token or settings.github_token).strip()
        if token:
            encoded = quote(token, safe="")
            return f"https://x-access-token:{encoded}@github.com/{owner}/{repo}.git"
        return f"https://github.com/{owner}/{repo}.git"

    def _build_headers(self, request_token: Optional[str]) -> Dict[str, str]:
        token = (request_token or settings.github_token).strip()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "impact-analysis-agent/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _raise_for_github_error(self, response: httpx.Response, owner: str, repo: str, pr_number: int) -> None:
        if response.status_code < 400:
            return

        if response.status_code == 404:
            raise ServiceError(
                f"PR #{pr_number} not found in {owner}/{repo}",
                status_code=404,
                code="pr_not_found",
            )
        if response.status_code in (401, 403):
            raise ServiceError(
                "GitHub access denied. Provide github_token in request or set GITHUB_TOKEN with repo access.",
                status_code=403,
                code="github_access_denied",
            )
        raise ServiceError(
            f"GitHub API error ({response.status_code}) while fetching PR data",
            status_code=502,
            code="github_api_error",
        )

    def _from_local_patch(self, patch_file: str, pr_number: int) -> Dict:
        with open(patch_file, "r", encoding="utf-8") as f:
            patch_text = f.read()

        files = []
        current_file = None
        current_patch_lines: List[str] = []
        additions = 0
        deletions = 0

        for line in patch_text.splitlines():
            if line.startswith("diff --git"):
                if current_file:
                    files.append(
                        {
                            "path": current_file,
                            "additions": additions,
                            "deletions": deletions,
                            "patch": "\n".join(current_patch_lines),
                        }
                    )
                parts = line.split(" ")
                current_file = parts[-1].replace("b/", "")
                current_patch_lines = [line]
                additions = 0
                deletions = 0
            else:
                current_patch_lines.append(line)
                if line.startswith("+") and not line.startswith("+++"):
                    additions += 1
                if line.startswith("-") and not line.startswith("---"):
                    deletions += 1

        if current_file:
            files.append(
                {
                    "path": current_file,
                    "additions": additions,
                    "deletions": deletions,
                    "patch": "\n".join(current_patch_lines),
                }
            )

        return {
            "pr_number": pr_number,
            "title": "Improve payment validation and schema consistency",
            "body": "Demo PR for impact analysis",
            "commit_messages": [
                "feat(payment): strengthen validation and add currency checks",
                "refactor(shared): normalize money formatting",
                "feat(order): persist transactionRef in order schema",
            ],
            "changed_files": files,
        }
