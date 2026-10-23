import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select, update
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
    underground connection never creates two sessions or two routes. The key is claimed before the
    request runs: a copy that arrives while the first one is still running gets 409
    REQUEST_IN_PROGRESS instead of running twice. The same key with a different body is rejected.
    Only successful responses are stored; a failed request releases its key so it can be retried.
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

        existing = await run_in_threadpool(_claim, key, fingerprint)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                return error_response(
                    request, ErrorCode.IDEMPOTENCY_KEY_REUSED, "This Idempotency-Key was used for a different request."
                )
            if existing.status_code is None:
                busy = error_response(request, ErrorCode.REQUEST_IN_PROGRESS, "The same request is still running.")
                busy.headers["Retry-After"] = "1"
                return busy
            return Response(existing.body, status_code=existing.status_code, media_type="application/json")

        completed = False
        try:
            response = await call_next(request)
            if not 200 <= response.status_code < 300:
                return response
            content = b"".join([chunk async for chunk in response.body_iterator])
            await run_in_threadpool(_complete, key, response.status_code, content)
            completed = True
            return Response(content, status_code=response.status_code, headers=dict(response.headers))
        finally:
            if not completed:
                await run_in_threadpool(_release, key)


def _claim(key: str, fingerprint: str) -> IdempotencyRow | None:
    """Claim the key for this request. Returns None when claimed, otherwise the record that holds it."""
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal.begin() as db:
        # Forget finished keys past their TTL, and claims whose request died before releasing them.
        db.execute(
            delete(IdempotencyRow).where(
                IdempotencyRow.key == key,
                or_(
                    IdempotencyRow.created_at < now - timedelta(seconds=settings.idempotency_ttl_seconds),
                    and_(
                        IdempotencyRow.status_code.is_(None),
                        IdempotencyRow.created_at < now - timedelta(seconds=settings.idempotency_claim_timeout_seconds),
                    ),
                ),
            )
        )
        # A concurrent claim on the same key makes this insert wait for that transaction, then do nothing.
        claimed = db.scalar(
            insert(IdempotencyRow)
            .values(key=key, fingerprint=fingerprint)
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(IdempotencyRow.key)
        )
        return None if claimed else db.scalar(select(IdempotencyRow).where(IdempotencyRow.key == key))


def _complete(key: str, status_code: int, body: bytes) -> None:
    with SessionLocal.begin() as db:
        db.execute(update(IdempotencyRow).where(IdempotencyRow.key == key).values(status_code=status_code, body=body))


def _release(key: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(delete(IdempotencyRow).where(IdempotencyRow.key == key, IdempotencyRow.status_code.is_(None)))
