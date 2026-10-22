from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.domain.localization import VisionFeatures, VisionText
from app.models import SessionRow
from tests.conftest import JPEG, Api, error_code


def _sees(api: Api, *texts: str) -> None:
    api.vision.features = VisionFeatures(image_quality="usable", texts=[VisionText(text=t) for t in texts])


def _event(api: Api, event_type: str, event_id: str, route_id: str) -> dict:
    return api.post(
        "/navigation-events",
        {
            "route_id": route_id,
            "events": [{"event_id": event_id, "type": event_type, "client_timestamp": datetime.now(UTC).isoformat()}],
        },
    )


def test_express_trip_from_line4_to_the_platform(api: Api):
    """The representative scenario end to end: ask, choose, locate, confirm, walk, buy, continue."""
    api.start()
    assert api.say("How do I pay?")["status"] == "answered"

    express = api.say("Express")
    assert express["destination"] == {"poi_id": "poi-express-tickets"}
    assert express["session"]["state"] == "needs_location"

    _sees(api, "공항철도", "Airport Railroad", "4호선")
    located = api.photo().json()
    assert located["status"] == "needs_confirmation"
    assert located["candidates"][0]["node_id"] == "line4-gates"

    confirmed = api.post(
        "/location-confirmations",
        {
            "expected_revision": located["session"]["revision"],
            "method": "photo_candidate",
            "localization_id": located["localization_id"],
            "selected_node_id": "line4-gates",
        },
    )
    assert confirmed["session"]["state"] == "ready_to_navigate"

    route = api.post(
        "/routes",
        {
            "expected_revision": api.revision,
            "destination_poi_id": "poi-express-tickets",
            "map_version": api.map_version,
        },
    )
    assert route["path_node_ids"] == ["line4-gates", "arex-concourse", "express-tickets"]
    assert route["steps"][0]["arrow_yaw_deg"] == -80

    assert _event(api, "navigation_started", "e1", route["route_id"])["session"]["state"] == "navigating"
    assert _event(api, "arrived", "e2", route["route_id"])["session"]["state"] == "arrived"

    machine = api.raw_post(
        "/messages", data={"message": "What should I press?"}, files={"image": ("machine.jpg", JPEG, "image/jpeg")}
    ).json()
    assert machine["assistant_message"].startswith("I can see a ticket machine")

    platform = api.say("Go to the platform")
    assert platform["destination"] == {"poi_id": "poi-express-platform"}
    assert platform["session"]["state"] == "ready_to_navigate"  # starts from the counter, no new photo

    next_leg = api.post(
        "/routes",
        {
            "expected_revision": api.revision,
            "destination_poi_id": "poi-express-platform",
            "map_version": api.map_version,
        },
    )
    assert next_leg["path_node_ids"][-1] == "express-platform"


def test_photo_without_match_asks_for_a_retake_and_manual_choice_still_works(api: Api):
    api.start()
    api.say("Express")

    no_match = api.photo().json()
    assert no_match["status"] == "no_match"
    assert no_match["retake_hint"]
    assert no_match["session"]["state"] == "needs_location"

    manual = api.post(
        "/location-confirmations",
        {"expected_revision": api.revision, "method": "manual", "selected_node_id": "ktx-hall"},
    )
    assert manual["location"]["floor_id"] == "2F"


def test_a_candidate_must_come_from_the_photo_result(api: Api):
    api.start()
    api.say("Express")
    _sees(api, "4호선", "Airport Railroad")
    located = api.photo().json()

    response = api.raw_post(
        "/location-confirmations",
        {
            "expected_revision": located["session"]["revision"],
            "method": "photo_candidate",
            "localization_id": located["localization_id"],
            "selected_node_id": "ktx-hall",
        },
    )

    assert error_code(response) == "INVALID_INPUT"


def test_stale_revision_is_rejected(api: Api):
    api.start()
    api.say("Express")

    response = api.raw_post(
        "/location-confirmations", {"expected_revision": 1, "method": "manual", "selected_node_id": "ktx-hall"}
    )

    assert response.status_code == 409
    assert error_code(response) == "REVISION_CONFLICT"
    assert response.json()["error"]["details"]["revision"] == api.revision


def test_actions_out_of_order_are_rejected(api: Api):
    api.start()

    photo = api.photo()  # no destination yet, so not locating
    route = api.raw_post(
        "/routes",
        {
            "expected_revision": api.revision,
            "destination_poi_id": "poi-express-tickets",
            "map_version": api.map_version,
        },
    )

    assert (photo.status_code, error_code(photo)) == (409, "INVALID_STATE")
    assert error_code(route) == "INVALID_INPUT"  # destination must come from /messages


def test_idempotency_key_replays_and_rejects_reuse(api: Api):
    api.start()
    body = {"message": "Express"}

    first = api.raw_post("/messages", body, key="key-1")
    replay = api.raw_post("/messages", body, key="key-1")
    reused = api.raw_post("/messages", {"message": "All-stop"}, key="key-1")
    missing = api.http.post(api.url("/messages"), json=body, headers={"Authorization": f"Bearer {api.token}"})

    assert replay.json() == first.json()
    assert error_code(reused) == "IDEMPOTENCY_KEY_REUSED"
    assert (missing.status_code, error_code(missing)) == (400, "BAD_REQUEST")


def test_session_token_rules(api: Api):
    api.start()
    own_token = api.token
    api.start()  # a second session

    other = api.http.get(
        f"/api/v1/sessions/{api.session_id}/navigation-state", headers={"Authorization": f"Bearer {own_token}"}
    )
    anonymous = api.http.get(f"/api/v1/sessions/{api.session_id}/navigation-state")
    with SessionLocal.begin() as db:
        db.execute(
            update(SessionRow)
            .where(SessionRow.id == api.session_id)
            .values(last_seen_at=datetime.now(UTC) - timedelta(hours=3))
        )
    expired = api.http.get(
        f"/api/v1/sessions/{api.session_id}/navigation-state", headers={"Authorization": f"Bearer {api.token}"}
    )

    assert (other.status_code, error_code(other)) == (403, "SESSION_FORBIDDEN")
    assert (anonymous.status_code, error_code(anonymous)) == (401, "SESSION_UNAUTHORIZED")
    assert (expired.status_code, error_code(expired)) == (401, "SESSION_EXPIRED")


def test_photo_upload_limits(api: Api):
    api.start()
    api.say("Express")

    png = api.raw_post(
        "/localizations",
        data={"map_version": api.map_version},
        files={"image": ("a.png", b"\x89PNG\r\n" + b"0" * 64, "image/png")},
    )
    huge = api.raw_post(
        "/localizations",
        data={"map_version": api.map_version},
        files={"image": ("a.jpg", JPEG + b"0" * (2 * 1024 * 1024), "image/jpeg")},
    )
    old_map = api.raw_post(
        "/localizations", data={"map_version": "2020-01-01.1"}, files={"image": ("a.jpg", JPEG, "image/jpeg")}
    )

    assert (png.status_code, error_code(png)) == (415, "UNSUPPORTED_IMAGE")
    assert (huge.status_code, error_code(huge)) == (413, "IMAGE_TOO_LARGE")
    assert (old_map.status_code, error_code(old_map)) == (409, "MAP_VERSION_MISMATCH")


def test_patch_cannot_set_a_destination(api: Api):
    api.start()

    response = api.http.patch(
        api.url(""), json={"expected_revision": api.revision, "destination_poi_id": "x"}, headers=api.headers()
    )

    assert error_code(response) == "INVALID_INPUT"


def test_maps_are_public(api: Api):
    graph = api.http.get("/api/v1/maps/seoul-station/graph").json()
    pois = api.http.get("/api/v1/maps/seoul-station/pois", params={"query": "직통"}).json()

    assert graph["edges"][0]["from"] == "ktx-hall"
    assert {p["poi_id"] for p in pois["pois"]} == {"poi-express-tickets", "poi-express-platform"}
