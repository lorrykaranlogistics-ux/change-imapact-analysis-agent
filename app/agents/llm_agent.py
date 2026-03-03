import json
import logging
from typing import Any, Dict, List

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMAgent:
    def __init__(self) -> None:
        self.api_key = settings.gemini_api_key.strip()
        self.model = settings.gemini_model
        self.api_base_url = settings.gemini_api_base_url.rstrip("/")
        self.client = httpx.Client(timeout=settings.gemini_timeout_seconds) if self.api_key else None

    def predict(
        self,
        pr_diff: str,
        graph_result: Dict[str, Any],
        changed_files: List[str],
        commit_messages: List[str],
    ) -> Dict[str, Any]:
        fallback = self._heuristic_predict(pr_diff, graph_result, changed_files, commit_messages)
        if not self.client:
            return fallback

        prompt = {
            "changed_files": changed_files,
            "commit_messages": commit_messages,
            "graph_result": graph_result,
            "diff": pr_diff[:12000],
        }

        try:
            response = self.client.post(
                f"{self.api_base_url}/models/{self.model}:generateContent",
                params={"key": self.api_key},
                json={
                    "contents": [
                        {
                            "parts": [
                                {
                                    "text": (
                                        "You are an enterprise DevOps impact analysis model. "
                                        "Return strict JSON with this shape only: "
                                        '{"breakingChange": boolean, "schemaChange": boolean, "logicChange": boolean, '
                                        '"configChange": boolean, "riskLevel": "LOW"|"MEDIUM"|"HIGH", '
                                        '"regressionAreas": string[], "suggestedTests": string[]}.\n\n'
                                        f"Input:\n{json.dumps(prompt)}"
                                    )
                                }
                            ]
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json",
                    },
                },
            )
            response.raise_for_status()
            response_json = response.json()
            raw_text = self._extract_response_text(response_json)
            if not raw_text:
                return fallback

            args = json.loads(raw_text)
            return {
                "classification": {
                    "breakingChange": args["breakingChange"],
                    "schemaChange": args["schemaChange"],
                    "logicChange": args["logicChange"],
                    "configChange": args["configChange"],
                },
                "riskLevel": args["riskLevel"],
                "regressionAreas": args["regressionAreas"],
                "suggestedTests": args["suggestedTests"],
            }
        except Exception as exc:
            logger.warning("LLM unavailable, using heuristic fallback", extra={"error": str(exc)})
            return fallback

    def _extract_response_text(self, payload: Dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        return "\n".join([t for t in texts if t]).strip()

    def _heuristic_predict(
        self,
        pr_diff: str,
        graph_result: Dict[str, Any],
        changed_files: List[str],
        _: List[str],
    ) -> Dict[str, Any]:
        lower_diff = pr_diff.lower()
        schema_change = "schema" in lower_diff or "model" in lower_diff or "transactionref" in lower_diff
        logic_change = "validate" in lower_diff or "if (" in lower_diff or "throw new error" in lower_diff
        config_change = ".env" in " ".join(changed_files).lower() or "config" in " ".join(changed_files).lower()
        breaking = "required" in lower_diff or "remove" in lower_diff

        risk = "LOW"
        if schema_change or len(graph_result.get("impacted_services", [])) >= 2:
            risk = "MEDIUM"
        if schema_change and logic_change and "payment-service" in graph_result.get("impacted_services", []):
            risk = "HIGH"

        regression_areas = [
            "payment retry logic",
            "order confirmation flow",
        ]
        suggested_tests = [
            "invalid card test",
            "timeout simulation test",
            "currency mismatch validation test",
        ]

        return {
            "classification": {
                "breakingChange": breaking,
                "schemaChange": schema_change,
                "logicChange": logic_change,
                "configChange": config_change,
            },
            "riskLevel": risk,
            "regressionAreas": regression_areas,
            "suggestedTests": suggested_tests,
        }

    def predict_heuristic(
        self,
        pr_diff: str,
        graph_result: Dict[str, Any],
        changed_files: List[str],
        commit_messages: List[str],
    ) -> Dict[str, Any]:
        return self._heuristic_predict(pr_diff, graph_result, changed_files, commit_messages)
