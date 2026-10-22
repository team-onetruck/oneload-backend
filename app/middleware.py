import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings
from app.db import SessionLocal
from app.errors import ErrorCode, error_response
from app.models import IdempotencyRow
from app.security import sha256_hex

log = logging.getLogger("hangil.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Tags every request with an id (echoed in errors and X-Request-ID) and logs one JSON line per request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = f"req_{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        log.info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
        )
        return response


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Every POST needs an Idempotency-Key (API spec v0.3 §3).

    A retried request with the same key and the same body gets the stored response back, so a flaky
    underground connection never creates two sessions or two routes. The same key with a different
    body is rejected. Only successful responses are stored; failed requests may be retried freely.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "POST":
            return await call_next(request)

        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 64:
            return error_response(request, ErrorCode.BAD_REQUEST, "Idempotency-Key header is required on POST.")

        body = await request.body()
        auth = request.headers.get("Authorization", "")
        fingerprint = sha256_hex(f"{request.url.path}\n{auth}\n".encode() + body)

        stored = await run_in_threadpool(_load, key)
        if stored is not None:
            if stored.fingerprint != fingerprint:
                return error_response(
                    request, ErrorCode.IDEMPOTENCY_KEY_REUSED, "This Idempotency-Key was used for a different request."
                )
            return Response(stored.body, status_code=stored.status_code, media_type="application/json")

        response = await call_next(request)
        if not 200 <= response.status_code < 300:
            return response
        content = b"".join([chunk async for chunk in response.body_iterator])
        await run_in_threadpool(_save, key, fingerprint, response.status_code, content)
        return Response(content, status_code=response.status_code, headers=dict(response.headers))


def _load(key: str) -> IdempotencyRow | None:
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().idempotency_ttl_seconds)
    with SessionLocal.begin() as db:
        db.execute(delete(IdempotencyRow).where(IdempotencyRow.key == key, IdempotencyRow.created_at < cutoff))
        return db.scalar(select(IdempotencyRow).where(IdempotencyRow.key == key))


def _save(key: str, fingerprint: str, status_code: int, body: bytes) -> None:
    # Two copies of the same request in flight at once both run and the first one stored wins.
    # Revision-checked endpoints reject the second state change, so the window only affects
    # append-only calls such as /messages.
    with SessionLocal.begin() as db:
        db.execute(
            insert(IdempotencyRow)
            .values(key=key, fingerprint=fingerprint, status_code=status_code, body=body)
            .on_conflict_do_nothing(index_elements=["key"])
        )
