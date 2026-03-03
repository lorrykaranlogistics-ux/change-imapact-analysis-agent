from app.agents.llm_agent import LLMAgent


class _RaisingClient:
    def post(self, *args, **kwargs):
        raise RuntimeError("quota exceeded")


def test_predict_falls_back_when_llm_call_fails():
    agent = LLMAgent()
    agent.client = _RaisingClient()

    result = agent.predict(
        pr_diff="schema validate if (x)",
        graph_result={"impacted_services": ["payment-service", "order-service"], "dependency_depth": 2},
        changed_files=["payment-service/src/server.js"],
        commit_messages=["feat: update schema"],
    )

    assert "classification" in result
    assert "riskLevel" in result
    assert "regressionAreas" in result
    assert "suggestedTests" in result
