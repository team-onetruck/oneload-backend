from fastapi.testclient import TestClient
from hangil_r2 import FakeLLM, LLMAgent, LLMVision, MockKnowledgeSearch

from app.ai.rule_agent import RuleAgent
from app.ai.vision import NoVision
from app.main import create_app, production_ai
from tests.conftest import Api


def test_production_ai_falls_back_without_server_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent, vision = production_ai()

    assert isinstance(agent, RuleAgent)
    assert isinstance(vision, NoVision)


def test_production_ai_uses_r2_when_server_key_exists(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    agent, vision = production_ai()

    assert isinstance(agent, LLMAgent)
    assert isinstance(vision, LLMVision)
    assert agent.vision is vision
    assert isinstance(agent.fallback, RuleAgent)


def test_r2_agent_runs_through_backend_message_api(map_store):
    llm = FakeLLM()
    vision = LLMVision(llm)
    agent = LLMAgent(llm, search_knowledge=MockKnowledgeSearch(), vision=vision, fallback=RuleAgent())

    with TestClient(create_app(map_store=map_store, agent=agent, vision=vision)) as http:
        api = Api(http=http, vision=vision)
        api.start("ko")
        response = api.say("공항티켓 어디서 구매해?")
        description = http.get("/api/v1/openapi.json").json()["info"]["description"]

    assert response["status"] == "needs_clarification"
    assert response["intent"] == "purchase_arex_ticket"
    assert [item["label"] for item in response["suggested_replies"]] == ["직통열차", "일반열차", "잘 모르겠어요"]
    assert "대화: `LLMAgent`" in description
    assert "사진: `LLMVision`" in description
