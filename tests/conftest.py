import os

# Point the app at the test database before any app module creates its engine.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://hangil:hangil@localhost:54329/hangil_test"
)

import uuid  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from httpx import Response  # noqa: E402
from sqlalchemy import create_engine, make_url, text  # noqa: E402

from app.config import ROOT  # noqa: E402
from app.db import engine  # noqa: E402
from app.domain.localization import VisionFeatures  # noqa: E402
from app.domain.mapdata import MapData, MapStore  # noqa: E402
from app.main import create_app  # noqa: E402

# A frozen copy of the Seoul Station map, so survey updates in oneload-data never change test outcomes.
FIXTURE_MAPS = Path(__file__).parent / "fixtures" / "maps"


def _ensure_test_database() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}) is None:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Build the test schema with the real migrations, so the migration itself is tested too."""
    _ensure_test_database()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE sessions, idempotency_keys CASCADE"))


@pytest.fixture(scope="session")
def map_store() -> MapStore:
    return MapStore.load(FIXTURE_MAPS)


@pytest.fixture(scope="session")
def seoul(map_store: MapStore) -> MapData:
    return map_store.get("seoul-station")


class FakeVision:
    """Stands in for the vision model: returns whatever a test says the photo shows."""

    def __init__(self) -> None:
        self.features = VisionFeatures(image_quality="no_signage")

    def extract(self, image: bytes) -> VisionFeatures:
        return self.features


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@dataclass
class Api:
    """Thin client that adds the headers every call needs (token, Idempotency-Key)."""

    http: TestClient
    vision: FakeVision
    session_id: str = ""
    token: str = ""
    revision: int = 0
    map_version: str = ""
    last: dict[str, Any] = field(default_factory=dict)

    def start(self, language: str = "en") -> dict[str, Any]:
        body = self.post(
            "/sessions",
            {"language": language, "map_id": "seoul-station", "client": {"platform": "web", "app_version": "test"}},
            auth=False,
        )
        self.session_id, self.token, self.map_version = body["session_id"], body["session_token"], body["map_version"]
        return body

    def url(self, path: str) -> str:
        return f"/api/v1/sessions/{self.session_id}{path}" if not path.startswith("/sessions") else f"/api/v1{path}"

    def headers(self, auth: bool = True, key: str | None = None) -> dict[str, str]:
        headers = {"Idempotency-Key": key or str(uuid.uuid4())}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def raw_post(self, path: str, json: Any = None, *, auth: bool = True, key: str | None = None, **kw) -> Response:
        return self.http.post(self.url(path), json=json, headers=self.headers(auth, key), **kw)

    def post(self, path: str, json: Any = None, *, auth: bool = True, **kw) -> dict[str, Any]:
        response = self.raw_post(path, json, auth=auth, **kw)
        assert response.status_code in (200, 201), response.text
        return self._remember(response.json())

    def say(self, message: str, **extra: Any) -> dict[str, Any]:
        return self.post("/messages", {"message": message, **extra})

    def photo(self, **form: str) -> Response:
        response = self.raw_post(
            "/localizations",
            data={"map_version": self.map_version, **form},
            files={"image": ("sign.jpg", JPEG, "image/jpeg")},
        )
        if response.status_code == 200:
            self._remember(response.json())
        return response

    def _remember(self, body: dict[str, Any]) -> dict[str, Any]:
        if "session" in body:
            self.revision = body["session"]["revision"]
        self.last = body
        return body


@pytest.fixture
def api(map_store: MapStore) -> Iterator[Api]:
    vision = FakeVision()
    with TestClient(create_app(map_store=map_store, vision=vision)) as http:
        yield Api(http=http, vision=vision)


def error_code(response: Response) -> str:
    return response.json()["error"]["code"]
