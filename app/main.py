import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from hangil_r2 import LLMAgent, LLMVision, OpenAILLM
from hangil_rag import search_knowledge

from app.ai.contracts import Agent, VisionExtractor
from app.ai.rule_agent import RuleAgent
from app.ai.vision import NoVision
from app.api import conversation, health, location, maps, plans, routes, sessions
from app.config import get_settings
from app.domain.mapdata import MapStore
from app.errors import install_error_handlers
from app.middleware import IdempotencyMiddleware, RequestContextMiddleware
from app.openapi import install_openapi

API_PREFIX = "/api/v1"


def production_ai() -> tuple[Agent, VisionExtractor]:
    """Use R2/OpenAI in deployment; retain deterministic stand-ins when no server secret exists."""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        logging.getLogger("hangil.ai").warning("OPENAI_API_KEY is missing; using RuleAgent and NoVision")
        return RuleAgent(), NoVision()

    llm = OpenAILLM()
    vision = LLMVision(llm)
    agent = LLMAgent(llm, search_knowledge=search_knowledge, vision=vision, fallback=RuleAgent())
    return agent, vision


def create_app(
    *, map_store: MapStore | None = None, agent: Agent | None = None, vision: VisionExtractor | None = None
) -> FastAPI:
    """Build the app. Tests pass their own collaborators; production uses the defaults below."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Docs live under the API prefix so a reverse proxy can route /api/* to this app as a whole.
    app = FastAPI(
        title="한길 API",
        version="0.1.0",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.maps = map_store or MapStore.load(settings.map_dir)
    app.state.agent = agent or RuleAgent()
    app.state.vision = vision or NoVision()

    for module in (health, sessions, maps, conversation, location, routes, plans):
        app.include_router(module.router, prefix=API_PREFIX)
    install_error_handlers(app)
    install_openapi(app)

    # Starlette runs the last-added middleware first: CORS → request id/logging → idempotency.
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
    return app


production_agent, production_vision = production_ai()
app = create_app(agent=production_agent, vision=production_vision)
