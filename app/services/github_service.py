import logging
import os
import re
import tempfile
from typing import Dict, List, Tuple

from git import Repo

logger = logging.getLogger(__name__)


class GitHubService:
    def _parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", repo_url)
        if not match:
            raise ValueError("Invalid GitHub repository URL")
        return match.group("owner"), match.group("repo")

    def fetch_pr_data(self, repo_url: str, pr_number: int) -> Dict:
        owner, repo = self._parse_repo_url(repo_url)
        # Demo-first implementation: reads from local sample PR artifact for reproducibility.
        local_pr_patch = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../sample-microservices-node/sample-pr/pr-24.patch")
        )
        if pr_number == 24 and os.path.exists(local_pr_patch):
            return self._from_local_patch(local_pr_patch, pr_number)

        # Fallback: clone and inspect branch refs like pull/<n>/head if available.
        with tempfile.TemporaryDirectory() as tmp:
            clone_url = f"https://github.com/{owner}/{repo}.git"
            logger.info("Cloning repository", extra={"repo": clone_url})
            git_repo = Repo.clone_from(clone_url, tmp, depth=200)
            target_ref = f"pull/{pr_number}/head"
            try:
                git_repo.git.fetch("origin", target_ref)
                git_repo.git.checkout("FETCH_HEAD")
            except Exception:
                raise RuntimeError(
                    "Unable to fetch PR ref. For demo use PR #24 with bundled sample patch."
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
