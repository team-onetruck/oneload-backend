from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.openapi import Tag
from app.schemas import HealthOut

router = APIRouter(tags=[Tag.HEALTH])


@router.get(
    "/health", summary="상태 점검", description="API와 DB 연결 상태입니다. 비밀값이나 내부 경로는 담지 않습니다."
)
def health() -> HealthOut:
    # No secrets or internal paths here; this endpoint is public (API spec v0.3 §8.14).
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        database = "unavailable"
    return HealthOut(
        status="ok" if database == "ok" else "degraded", api_version="v1", dependencies={"database": database}
    )
