from typing import Dict


class RiskEngine:
    severity_weight = {
        "LOW": 1.0,
        "MEDIUM": 1.3,
        "HIGH": 1.6,
    }

    core_services = {"api-gateway", "order-service", "payment-service"}

    def score(
        self,
        lines_changed: int,
        dependency_depth: int,
        changed_services: set,
        llm_risk_level: str,
    ) -> Dict:
        core_multiplier = 1.5 if self.core_services.intersection(changed_services) else 1.0
        llm_weight = self.severity_weight.get(llm_risk_level.upper(), 1.0)

        raw = (lines_changed * 0.3) + (dependency_depth * 12 * 0.4)
        weighted = raw * core_multiplier * llm_weight
        bounded = max(1, min(100, int(round(weighted))))

        if bounded >= 75:
            level = "HIGH"
        elif bounded >= 40:
            level = "MEDIUM"
        else:
            level = "LOW"

        confidence = round(min(0.99, 0.55 + (dependency_depth * 0.08) + (0.08 if level == "HIGH" else 0.03)), 2)

        return {
            "riskScore": bounded,
            "riskLevel": level,
            "confidence": confidence,
        }
