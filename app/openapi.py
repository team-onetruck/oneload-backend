"""Swagger UI (/api/v1/docs) additions that FastAPI cannot infer from the endpoints."""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

DESCRIPTION = """
Seoul Station / AREX guidance API (API spec v0.3 with system design v0.2 changes).

**Trying it here**

1. `POST /sessions` with
   `{"language": "en", "map_id": "seoul-station", "client": {"platform": "web", "app_version": "swagger"}}`.
2. Click **Authorize** and paste `session_token`.
3. Every `POST` needs an `Idempotency-Key` header: any new UUID per request. Reusing one replays the first response.
4. Typical flow: `messages` ("How do I pay?", "Express") → `location-confirmations`
   (method `manual`, node `line4-gates`) → `routes` → `navigation-events`.
   Calls that change state need the current `expected_revision` from the last response.

Photo recognition is not connected to OpenAI yet, so `localizations` answers `503 AI_SERVICE_UNAVAILABLE`;
use a `manual` location confirmation instead. Map data other than AREX floors is a placeholder until the field survey.
"""

_IDEMPOTENCY_HEADER = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": True,
    "description": "A new UUID per request. A retry with the same key and body returns the stored response.",
    "schema": {"type": "string", "maxLength": 64},
}


def install_openapi(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, description=DESCRIPTION, routes=app.routes)
            # The header is enforced by IdempotencyMiddleware, so FastAPI does not see it on any endpoint.
            for path in schema["paths"].values():
                if "post" in path:
                    path["post"].setdefault("parameters", []).append(_IDEMPOTENCY_HEADER)
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
