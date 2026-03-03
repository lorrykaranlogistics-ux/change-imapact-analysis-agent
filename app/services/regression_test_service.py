import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import settings

class RegressionTestService:
    def __init__(self) -> None:
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        self.test_path = os.path.join(self.project_root, "tests")
        self.command = ["python3", "-m", "pytest", "-q", "tests"]
        self.github_api_base_url = settings.github_api_base_url.rstrip("/")
        self.github_timeout = settings.github_api_timeout_seconds

    def skipped(self, reason: str) -> Dict[str, Any]:
        return {
            "status": "SKIPPED",
            "reason": reason,
            "command": " ".join(self.command),
            "summary": {"passed": 0, "failed": 0, "errors": 0, "skipped": 0},
            "exitCode": None,
            "durationSeconds": 0.0,
            "outputSnippet": "",
        }

    def run(self, repo_url: Optional[str] = None, pr_number: Optional[int] = None, github_token: Optional[str] = None) -> Dict[str, Any]:
        if repo_url and pr_number:
            github_result = self._run_on_github_actions(repo_url=repo_url, pr_number=pr_number, github_token=github_token)
            if github_result.get("status") != "SKIPPED":
                return github_result
        return self._run_local()

    def _run_local(self) -> Dict[str, Any]:
        if not os.path.isdir(self.test_path):
            return self.skipped("tests directory not available in runtime image")

        start = time.time()
        try:
            proc = subprocess.run(
                self.command,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = round(time.time() - start, 2)
            output = self._to_text(exc.stdout) + "\n" + self._to_text(exc.stderr)
            return {
                "status": "TIMEOUT",
                "reason": "regression tests timed out",
                "command": " ".join(self.command),
                "summary": {"passed": 0, "failed": 0, "errors": 0, "skipped": 0},
                "exitCode": None,
                "durationSeconds": duration,
                "outputSnippet": self._tail(output),
            }
        except Exception as exc:
            duration = round(time.time() - start, 2)
            return {
                "status": "FAILED",
                "reason": "regression test runner error",
                "command": " ".join(self.command),
                "summary": {"passed": 0, "failed": 0, "errors": 1, "skipped": 0},
                "exitCode": None,
                "durationSeconds": duration,
                "outputSnippet": self._tail(str(exc)),
            }

        duration = round(time.time() - start, 2)
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        summary = self._parse_pytest_summary(output)
        status = "PASSED" if proc.returncode == 0 else "FAILED"

        return {
            "status": status,
            "command": " ".join(self.command),
            "summary": summary,
            "exitCode": proc.returncode,
            "durationSeconds": duration,
            "outputSnippet": self._tail(output),
        }

    def _run_on_github_actions(self, repo_url: str, pr_number: int, github_token: Optional[str]) -> Dict[str, Any]:
        token = (github_token or settings.github_token).strip()
        if not token:
            return self.skipped("github_token is required to trigger GitHub Actions workflow")

        try:
            owner, repo = self._parse_repo_url(repo_url)
        except ValueError as exc:
            return self.skipped(str(exc))

        workflow_file = settings.github_workflow_file
        command = f"github-actions:{owner}/{repo}:{workflow_file}"
        dispatched_at = datetime.now(timezone.utc)
        headers = self._build_headers(token)
        start = time.time()

        try:
            with httpx.Client(timeout=self.github_timeout) as client:
                ref, dispatch_error = self._dispatch_workflow(
                    client=client,
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    workflow_file=workflow_file,
                    headers=headers,
                )
                if not ref:
                    return self._failed_github_result(
                        reason=dispatch_error or "Failed to dispatch workflow",
                        command=command,
                        duration=round(time.time() - start, 2),
                        output=dispatch_error or "",
                    )
                command = f"{command}@{ref}"

                run_obj = self._wait_for_dispatched_run(client, owner, repo, workflow_file, ref, dispatched_at, headers)
                if not run_obj:
                    return {
                        "status": "TIMEOUT",
                        "reason": "Timed out while waiting for dispatched GitHub workflow run to appear",
                        "command": command,
                        "summary": {"passed": 0, "failed": 0, "errors": 1, "skipped": 0},
                        "exitCode": None,
                        "durationSeconds": round(time.time() - start, 2),
                        "outputSnippet": "",
                    }

                run_id = run_obj["id"]
                final_run_obj = self._wait_for_workflow_completion(client, owner, repo, run_id, headers)
                if final_run_obj.get("status") != "completed":
                    return {
                        "status": "TIMEOUT",
                        "reason": "GitHub workflow run did not complete before timeout",
                        "command": command,
                        "summary": {"passed": 0, "failed": 0, "errors": 1, "skipped": 0},
                        "exitCode": None,
                        "durationSeconds": round(time.time() - start, 2),
                        "outputSnippet": self._tail(final_run_obj.get("html_url", "")),
                    }

                jobs = self._fetch_run_jobs(client, owner, repo, run_id, headers)
                summary = self._summarize_jobs(jobs)
                conclusion = final_run_obj.get("conclusion")
                status = self._map_github_conclusion_to_status(conclusion)

                return {
                    "status": status,
                    "command": command,
                    "summary": summary,
                    "exitCode": 0 if status == "PASSED" else 1,
                    "durationSeconds": round(time.time() - start, 2),
                    "outputSnippet": self._tail(
                        f"run_id={run_id}\nstatus={final_run_obj.get('status')}\n"
                        f"conclusion={conclusion}\nurl={final_run_obj.get('html_url', '')}"
                    ),
                }
        except httpx.RequestError as exc:
            return self._failed_github_result(
                reason="Failed to communicate with GitHub API for regression workflow",
                command=command,
                duration=round(time.time() - start, 2),
                output=str(exc),
            )
        except Exception as exc:
            return self._failed_github_result(
                reason="Unexpected error while running regression workflow on GitHub",
                command=command,
                duration=round(time.time() - start, 2),
                output=str(exc),
            )

    def _parse_repo_url(self, repo_url: str) -> Tuple[str, str]:
        match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", repo_url)
        if not match:
            raise ValueError("Invalid GitHub repository URL")
        return match.group("owner"), match.group("repo")

    def _build_headers(self, token: str) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "impact-analysis-agent/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _dispatch_workflow(
        self,
        client: httpx.Client,
        owner: str,
        repo: str,
        pr_number: int,
        workflow_file: str,
        headers: Dict[str, str],
    ) -> Tuple[Optional[str], Optional[str]]:
        dispatch_url = f"{self.github_api_base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
        refs = self._resolve_dispatch_refs(client, owner, repo, pr_number, headers)
        last_422 = ""

        for ref in refs:
            dispatch_payload = {
                "ref": ref,
                "inputs": {
                    "pr_number": str(pr_number),
                },
            }
            dispatch_resp = client.post(dispatch_url, headers=headers, json=dispatch_payload)
            if dispatch_resp.status_code in (200, 201, 204):
                return ref, None
            if dispatch_resp.status_code == 422:
                last_422 = self._tail(dispatch_resp.text or "")
                continue

            reason, output = self._dispatch_error_details(
                client=client,
                owner=owner,
                repo=repo,
                workflow_file=workflow_file,
                ref=ref,
                headers=headers,
                status_code=dispatch_resp.status_code,
                response_text=dispatch_resp.text or "",
            )
            return None, f"{reason}\n{output}"

        if last_422:
            return None, (
                "Workflow dispatch rejected for all candidate refs (422). "
                "Check workflow file, branch refs, and required workflow inputs.\n"
                f"tried_refs={', '.join(refs)}\n{last_422}"
            )
        return None, "Workflow dispatch failed without a successful response."

    def _resolve_dispatch_refs(
        self,
        client: httpx.Client,
        owner: str,
        repo: str,
        pr_number: int,
        headers: Dict[str, str],
    ) -> list[str]:
        refs: list[str] = []
        configured_ref = settings.github_workflow_ref.strip()
        if configured_ref:
            refs.append(configured_ref)

        pr_url = f"{self.github_api_base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
        pr_resp = client.get(pr_url, headers=headers)
        if pr_resp.status_code == 200:
            pr_obj = pr_resp.json()
            head_ref = (pr_obj.get("head") or {}).get("ref")
            base_ref = (pr_obj.get("base") or {}).get("ref")
            if head_ref:
                refs.insert(0, head_ref)
            if base_ref:
                refs.insert(1 if head_ref else 0, base_ref)

        refs.extend(["main", "master"])
        # de-duplicate while preserving order
        return list(dict.fromkeys([r for r in refs if r]))

    def _wait_for_dispatched_run(
        self,
        client: httpx.Client,
        owner: str,
        repo: str,
        workflow_file: str,
        ref: str,
        dispatched_at: datetime,
        headers: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        deadline = time.time() + settings.github_workflow_lookup_timeout_seconds
        url = f"{self.github_api_base_url}/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs"
        while time.time() < deadline:
            response = client.get(
                url,
                headers=headers,
                params={"event": "workflow_dispatch", "branch": ref, "per_page": 10},
            )
            if response.status_code >= 400:
                return None
            runs = response.json().get("workflow_runs", [])
            for run in runs:
                created_at_str = run.get("created_at")
                if not created_at_str:
                    continue
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_at >= dispatched_at:
                    return run
            time.sleep(settings.github_workflow_poll_seconds)
        return None

    def _wait_for_workflow_completion(
        self,
        client: httpx.Client,
        owner: str,
        repo: str,
        run_id: int,
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        deadline = time.time() + settings.github_workflow_timeout_seconds
        url = f"{self.github_api_base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        last_payload: Dict[str, Any] = {}
        while time.time() < deadline:
            response = client.get(url, headers=headers)
            if response.status_code >= 400:
                return last_payload
            last_payload = response.json()
            if last_payload.get("status") == "completed":
                return last_payload
            time.sleep(settings.github_workflow_poll_seconds)
        return last_payload

    def _fetch_run_jobs(
        self,
        client: httpx.Client,
        owner: str,
        repo: str,
        run_id: int,
        headers: Dict[str, str],
    ) -> list[Dict[str, Any]]:
        url = f"{self.github_api_base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        response = client.get(url, headers=headers, params={"per_page": 100})
        if response.status_code >= 400:
            return []
        return response.json().get("jobs", [])

    def _summarize_jobs(self, jobs: list[Dict[str, Any]]) -> Dict[str, int]:
        counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for job in jobs:
            conclusion = job.get("conclusion")
            if conclusion == "success":
                counts["passed"] += 1
            elif conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
                counts["failed"] += 1
            elif conclusion in {"neutral", "skipped"}:
                counts["skipped"] += 1
            elif conclusion:
                counts["errors"] += 1
        return counts

    def _map_github_conclusion_to_status(self, conclusion: Optional[str]) -> str:
        if conclusion == "success":
            return "PASSED"
        if conclusion == "timed_out":
            return "TIMEOUT"
        if conclusion in {"skipped", "neutral"}:
            return "SKIPPED"
        return "FAILED"

    def _failed_github_result(self, reason: str, command: str, duration: float, output: str) -> Dict[str, Any]:
        return {
            "status": "FAILED",
            "reason": reason,
            "command": command,
            "summary": {"passed": 0, "failed": 1, "errors": 0, "skipped": 0},
            "exitCode": 1,
            "durationSeconds": duration,
            "outputSnippet": self._tail(output),
        }

    def _dispatch_error_details(
        self,
        client: httpx.Client,
        owner: str,
        repo: str,
        workflow_file: str,
        ref: str,
        headers: Dict[str, str],
        status_code: int,
        response_text: str,
    ) -> Tuple[str, str]:
        if status_code == 404:
            workflows_url = f"{self.github_api_base_url}/repos/{owner}/{repo}/actions/workflows"
            workflows_resp = client.get(workflows_url, headers=headers, params={"per_page": 100})
            if workflows_resp.status_code == 200:
                workflows = workflows_resp.json().get("workflows", [])
                workflow_files = {w.get("path", "").split("/")[-1] for w in workflows}
                if workflow_file not in workflow_files:
                    available = ", ".join(sorted([w for w in workflow_files if w]))
                    return (
                        "Workflow file not found in repository/ref. Set GITHUB_WORKFLOW_FILE to an existing file.",
                        f"requested={workflow_file} ref={ref}\navailable={available}",
                    )
            return (
                "Workflow dispatch endpoint not found or inaccessible. Verify token repo visibility and workflow file.",
                self._tail(response_text),
            )

        if status_code == 401:
            return (
                "GitHub token unauthorized for workflow dispatch. Provide a valid token.",
                self._tail(response_text),
            )

        if status_code == 403:
            return (
                "GitHub token lacks Actions write permission for workflow dispatch.",
                self._tail(response_text),
            )

        return (f"Failed to dispatch workflow ({status_code})", self._tail(response_text))

    def _parse_pytest_summary(self, output: str) -> Dict[str, int]:
        counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for key in counts:
            match = re.search(rf"(\\d+)\\s+{key}", output)
            if match:
                counts[key] = int(match.group(1))
        return counts

    def _tail(self, text: str, max_lines: int = 20) -> str:
        lines = [line for line in text.strip().splitlines() if line.strip()]
        return "\n".join(lines[-max_lines:])

    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
