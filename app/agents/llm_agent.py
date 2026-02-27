import json
import logging
from typing import Any, Dict, List

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class LLMAgent:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

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

        tool_schema = {
            "type": "function",
            "function": {
                "name": "impact_assessment",
                "description": "Classify change risk and suggest regression tests",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "breakingChange": {"type": "boolean"},
                        "schemaChange": {"type": "boolean"},
                        "logicChange": {"type": "boolean"},
                        "configChange": {"type": "boolean"},
                        "riskLevel": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                        "regressionAreas": {"type": "array", "items": {"type": "string"}},
                        "suggestedTests": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "breakingChange",
                        "schemaChange",
                        "logicChange",
                        "configChange",
                        "riskLevel",
                        "regressionAreas",
                        "suggestedTests",
                    ],
                },
            },
        }

        try:
            response = self.client.chat.completions.create(
                model=settings.openai_model,
                temperature=0.1,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an enterprise DevOps impact analysis model. Always use the function call.",
                    },
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                tools=[tool_schema],
                tool_choice={"type": "function", "function": {"name": "impact_assessment"}},
            )

            tool_calls = response.choices[0].message.tool_calls
            if not tool_calls:
                return fallback

            args = json.loads(tool_calls[0].function.arguments)
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
