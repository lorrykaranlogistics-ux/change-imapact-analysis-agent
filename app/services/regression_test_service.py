import os
import re
import subprocess
import time
from typing import Any, Dict


class RegressionTestService:
    def __init__(self) -> None:
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        self.test_path = os.path.join(self.project_root, "tests")
        self.command = ["python3", "-m", "pytest", "-q", "tests"]

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

    def run(self) -> Dict[str, Any]:
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
            output = (exc.stdout or "") + "\n" + (exc.stderr or "")
            return {
                "status": "TIMEOUT",
                "reason": "regression tests timed out",
                "command": " ".join(self.command),
                "summary": {"passed": 0, "failed": 0, "errors": 0, "skipped": 0},
                "exitCode": None,
                "durationSeconds": duration,
                "outputSnippet": self._tail(output),
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

