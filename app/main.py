import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.contracts import Agent, VisionExtractor
from app.ai.rule_agent import RuleAgent
from app.ai.vision import NoVision
from app.api import conversation, health, location, maps, routes, sessions
from app.config import get_settings
from app.domain.mapdata import MapStore
from app.errors import install_error_handlers
from app.middleware import IdempotencyMiddleware, RequestContextMiddleware

API_PREFIX = "/api/v1"


def create_app(
    *, map_store: MapStore | None = None, agent: Agent | None = None, vision: VisionExtractor | None = None
) -> FastAPI:
    """Build the app. Tests pass their own collaborators; production uses the defaults below."""
    settings = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Docs live under the API prefix so a reverse proxy can route /api/* to this app as a whole.
    app = FastAPI(
        title="Hangil API",
        version="0.1.0",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    app.state.maps = map_store or MapStore.load(settings.map_dir)
    app.state.agent = agent or RuleAgent()
    app.state.vision = vision or NoVision()

    for module in (health, sessions, maps, conversation, location, routes):
        app.include_router(module.router, prefix=API_PREFIX)
    install_error_handlers(app)

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


app = create_app()
