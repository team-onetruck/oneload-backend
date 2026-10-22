# Hangil (한길) backend

FastAPI + PostgreSQL API for Hangil's Seoul Station / AREX guidance: sessions, the indoor map, routing with street-view arrows, photo localization and the conversational entry point.

Contracts follow *API·시퀀스 설계 v0.3* with the changes in *시스템·AI 설계서 v0.2* (street view, `/messages` photo upload, `tip` / `unverified_notes`). The OpenAPI document is served at `/api/v1/docs`.

## Run locally

```sh
docker compose up -d --wait          # Postgres 17 + pgvector on 127.0.0.1:54329 (also creates hangil_test)
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload # http://127.0.0.1:8000/api/v1/docs
```

```sh
uv run pytest            # uses the hangil_test database; override with TEST_DATABASE_URL
uv run ruff check . && uv run ruff format .
```

Settings come from environment variables or `.env` (see `app/config.py`): `DATABASE_URL`, `CORS_ORIGINS`, limits and TTLs.

## Layout

| Path | What lives there |
| --- | --- |
| `app/api/` | Endpoints. `deps.py` holds auth, revision checks and state transitions shared by all of them |
| `app/domain/` | Pure logic, no I/O: map model, session state machine, routing, sign-text location matcher |
| `app/ai/` | Contracts with the AI modules (`contracts.py`), the output guard, and today's stand-ins |
| `app/middleware.py` | Request id + JSON access log, `Idempotency-Key` handling |
| `app/models.py`, `migrations/` | Tables and Alembic migrations |
| `data/maps/` | Map files, loaded at startup and validated (unknown node references fail the boot) |
| `deploy/macserver/` | Deploy script, Caddyfile and launchd agents for the demo server |

## AI modules: what is real today

| Piece | Today | Next |
| --- | --- | --- |
| Agent (`/messages`) | `RuleAgent`: keyword rules for the Seoul Station payment scenario, same contract and guard | OpenAI-backed agent (R2) implementing `Agent` |
| Photo → text (`/localizations`) | `NoVision`: answers **503 `AI_SERVICE_UNAVAILABLE`**, so the client offers manual selection | OpenAI vision extractor implementing `VisionExtractor` |
| Text → location | `match_location`: real, deterministic sign-text matcher | Unchanged |
| Knowledge (RAG) | Fixed citations inside `RuleAgent` | R3 module on pgvector |

Swapping one in is a change to `create_app()` defaults in `app/main.py`; no endpoint changes.

## Map data

`data/maps/seoul-station.json`. Only the floors of the AREX ticket points (express B2, all-stop B3) and platforms (B7) are confirmed, from the [AREX official guide](https://www.airportrailroad.com/co/steUsfGud.do) on 2026-10-22. Node positions, passages, distances, headings, sign texts and panoramas are placeholders until the field survey; the panoramas are CC0 photos of other stations. Bump `version` whenever the file changes, so clients holding the old map get `409 MAP_VERSION_MISMATCH`.

## Deploy

Every push to `main` runs lint and tests on GitHub, then the `deploy` job runs `deploy/macserver/deploy.sh` on the self-hosted runner on macserver (label `hangil`). It syncs the code to `~/hangil-api`, starts Postgres with Docker (Colima), migrates, and (re)loads two launchd agents:

- `com.onetruck.hangil-api`: uvicorn on `127.0.0.1:8000`, logs in `~/Library/Logs/hangil-api.log`
- `com.onetruck.hangil-caddy`: Caddy on `127.0.0.1:4173`, serving the web client from `~/hangil-demo/dist` and proxying `/api/*` to the API

Tailscale Funnel exposes Caddy at `https://baki-macbookpro.tail3286a4.ts.net:10000/` (API under `/api/v1`).

### Settings and secrets

Server settings live only in **GitHub repository secrets**. On every deploy, each secret (except `GITHUB_TOKEN`) is written to `~/hangil-api.env` on macserver, mode 600, replacing the previous file, and the API reads it at start-up. Never edit that file by hand or commit a value.

```sh
gh secret set OPENAI_API_KEY -R team-onetruck/oneload-backend   # prompts for the value
gh workflow run ci.yml -R team-onetruck/oneload-backend          # redeploy so the API picks it up
```

Removing a secret and redeploying removes it from the server as well.
