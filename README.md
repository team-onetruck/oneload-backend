# Hangil (한길) backend

FastAPI + PostgreSQL API for Hangil's Seoul Station / AREX guidance: sessions, the indoor map, routing with street-view arrows, photo localization and the conversational entry point.

Contracts follow *API·시퀀스 설계 v0.3* with the changes in *시스템·AI 설계서 v0.2* (street view, `/messages` photo upload, `tip` / `unverified_notes`). The OpenAPI document is served at `/api/v1/docs`.

This repo holds code only. Map data and photos live in [oneload-data](https://github.com/team-onetruck/oneload-data); the server setup and deploy script live in [oneload-infra](https://github.com/team-onetruck/oneload-infra).

## Run locally

Clone oneload-data next to this repo (the default `MAP_DIR` is `../oneload-data/maps`):

```sh
git clone https://github.com/team-onetruck/oneload-data ../oneload-data
docker compose up -d --wait          # Postgres 17 + pgvector on 127.0.0.1:54329
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload # http://127.0.0.1:8000/api/v1/docs
```

```sh
uv run pytest            # creates and uses hangil_test; override with TEST_DATABASE_URL
uv run ruff check . && uv run ruff format .
```

Settings come from environment variables or `.env` (see `app/config.py`): `DATABASE_URL`, `MAP_DIR`, `CORS_ORIGINS`, limits and TTLs.

## Layout

| Path | What lives there |
| --- | --- |
| `app/api/` | Endpoints. `deps.py` holds auth, revision checks and state transitions shared by all of them |
| `app/domain/` | Pure logic, no I/O: map model, session state machine, routing, sign-text location matcher |
| `app/ai/` | Contracts with the AI modules (`contracts.py`), the output guard, and today's stand-ins |
| `app/middleware.py` | Request id + JSON access log, `Idempotency-Key` handling |
| `app/openapi.py` | Korean Swagger texts, tags and the documented `Idempotency-Key` header |
| `app/models.py`, `migrations/` | Tables and Alembic migrations |
| `tests/fixtures/maps/` | Frozen copy of the Seoul Station map, so survey updates never change test outcomes |
| `Dockerfile` | API image: uv-installed dependencies, runs as a non-root user, health-checked on `/api/v1/health` |

## AI modules: what is real today

| Piece | Production | Fallback / next |
| --- | --- | --- |
| Agent (`/messages`) | R2 `LLMAgent` + `OpenAILLM` when `OPENAI_API_KEY` is set | Missing key: `RuleAgent` |
| Photo → text (`/localizations`) | R2 `LLMVision` using the same OpenAI client | Missing key: `NoVision` → 503 and manual selection |
| Text → location | Deterministic `match_location` | Unchanged |
| Knowledge (RAG) | Packaged `MockKnowledgeSearch` demo documents | Replace with the R3 pgvector service |

`app/main.py` selects the OpenAI components at process startup. The API endpoints and response schemas do not change.

## CI and deploy

`.github/workflows/ci.yml` runs lint and tests on every push, checks that the current oneload-data still loads with this code, and builds the `Dockerfile` to catch image breakage. On `main` the `deploy` job runs on the self-hosted runner on macserver, checks out this repo, oneload-infra and oneload-data, and runs `macserver/deploy.sh` from oneload-infra, which builds the image on the server and runs it with Postgres and Caddy as the compose project `hangil` (details and the Portainer dashboard in that repo).

Redeploy without a code change (after editing data, infra or a secret):

```sh
gh workflow run ci.yml -R team-onetruck/oneload-backend
```

### Settings and secrets

Server settings live only in **GitHub repository secrets** of this repo. The deploy step maps each one to an `APP_*` variable (`APP_OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}`), and the deploy script writes every non-empty one to `~/hangil-api.env` on macserver (mode 600, prefix removed, previous file replaced). Never edit that file by hand or commit a value.

```sh
gh secret set OPENAI_API_KEY -R team-onetruck/oneload-backend   # prompts for the value
gh workflow run ci.yml -R team-onetruck/oneload-backend          # redeploy so the API picks it up
```

A new setting needs the secret plus one mapping line in the workflow. Deleting a secret and redeploying removes it from the server.
