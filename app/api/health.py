from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health")
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
