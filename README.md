# Hangil (한길) backend

FastAPI + PostgreSQL API for Hangil's Seoul Station / AREX guidance: sessions, the indoor map, routing with street-view arrows, photo localization and the conversational entry point.

Contracts follow *API·시퀀스 설계 v0.3* with the changes in *시스템·AI 설계서 v0.2* (street view, `/messages` photo upload, `tip` / `unverified_notes`). The OpenAPI document is served at `/api/v1/docs`.

This repo holds code only. Map data and photos live in [oneload-data](https://github.com/team-onetruck/oneload-data); the server setup and deploy script live in [oneload-infra](https://github.com/team-onetruck/oneload-infra).

## Run locally

Clone oneload-data and oneload_agent next to this repo. The default `MAP_DIR` is `../oneload-data/maps`, and the agent is installed from `../oneload_agent` as an editable package, so `git pull` in that folder (or an edit there) is picked up on the next start with no change here:

```sh
git clone https://github.com/team-onetruck/oneload-data ../oneload-data
git clone https://github.com/team-onetruck/oneload_agent ../oneload_agent
docker compose up -d --wait          # Postgres 17 + pgvector on 127.0.0.1:54329
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload # http://127.0.0.1:8000/api/v1/docs
```

The OpenAI agent turns on when the `OPENAI_API_KEY` environment variable is set when the API starts (`export OPENAI_API_KEY=...`; the backend's `.env` is not read for it). Without it the API uses `RuleAgent` and `NoVision`.

```sh
uv run pytest            # creates and uses hangil_test; override with TEST_DATABASE_URL
uv run ruff check . && uv run ruff format .
```

Settings come from environment variables or `.env` (see `app/config.py`): `DATABASE_URL`, `MAP_DIR`, `CORS_ORIGINS`, limits and TTLs.

## Layout

| Path | What lives there |
| --- | --- |
| `app/api/` | Endpoints. `deps.py` holds auth, revision checks and state transitions shared by all of them |
| `app/domain/` | Pure logic, no I/O: map model, session state machine, routing (multi-stop, accessibility), task planner and rules, sign-text location matcher |
| `app/ai/` | Contracts with the AI modules (`contracts.py`), the output guard, and today's stand-ins |
| `app/middleware.py` | Request id + JSON access log, `Idempotency-Key` handling |
| `app/openapi.py` | Korean Swagger texts, tags and the documented `Idempotency-Key` header |
| `app/models.py`, `migrations/` | Tables and Alembic migrations |
| `tests/fixtures/maps/` | Frozen copy of the Seoul Station map, so survey updates never change test outcomes |
| `tests/fixtures/mock-station.json` | Invented station for planner tests: fare zones, a closed refund machine, stairs versus elevator |
| `Dockerfile` | API image: uv-installed dependencies, runs as a non-root user, health-checked on `/api/v1/health` |

## Action plan (task planner, rule engine)

Once `/messages` sets a destination, the session gets a **plan**: the steps to reach it, in order (`app/domain/planning.py`). It is returned by `/messages`, `/routes`, `PATCH /sessions/{id}` and `GET /sessions/{id}/plan`.

| Step | Comes from |
| --- | --- |
| `BUY_TICKET` (required) | Rule `BUY_EXPRESS_TICKET` (express train without an express ticket) or `BUY_SINGLE_USE_CARD` (all-stop train with no ticket at all) |
| `REFUND_SINGLE_USE_CARD` (recommended) | Rule for leaving with an unrefunded single-use card |
| `PASS_GATE` (required) | Every ticket gate the computed route crosses |
| `MOVE_TO_DESTINATION` (required) | Always last |

- **Rules cite a source.** Each rule in `RULES` links the official AREX page it comes from. A rule whose facts are unknown is not guessed: the fact goes to `plan.missing_facts`, and the client sends it with `PATCH /sessions/{id}` (`ticket_type`, `refund_status`). A new answer the user gives the agent also updates the facts (slots `has_transit_card`, `has_single_use_card`, `single_use_card_returned` that changed this turn), except facts the client set with `PATCH`, which only the client changes. An earlier answer never undoes a finished step.
- **Recommended steps are offered, not imposed.** They join the route only after `POST /steps/{id}/accept`; `/skip` declines them. Required steps cannot be skipped.
- **Stops are chosen for the whole walk.** When several places can do a step (e.g. refund machines), `/routes` picks the combination with the shortest total walk, not the nearest machine. Closed places (`closed: true` on a POI) are never chosen; with none open the step is `BLOCKED` and the trip goes on.
- **Progress.** `POST /steps/{id}/complete` finishes the current step, moves the user's location there and updates facts (ticket bought, card refunded, now inside or outside the gates). After a change, reroute with `POST /routes` and `reroute: true`.
- **Map fields the planner uses** (all optional, so current data still loads): node `area` (`paid` / `free`), edge `closed`, POI `closed`, POI categories `tickets`, `platform`, `exit`, `card_refund`. The Seoul Station data has none of these surveyed yet; `tests/fixtures/mock-station.json` exercises them.
- **Accessibility.** Preferences `avoid_stairs` (no stairs) and `wheelchair` (no stairs or escalators).

## AI modules: what is real today

| Piece | Production | Fallback / next |
| --- | --- | --- |
| Agent (`/messages`) | R2 `LLMAgent` + `OpenAILLM` when `OPENAI_API_KEY` is set | Missing key: `RuleAgent` |
| Photo → text (`/localizations`) | R2 `LLMVision` using the same OpenAI client | Missing key: `NoVision` → 503 and manual selection |
| Text → location | Deterministic `match_location` | Unchanged |
| Knowledge (RAG) | R3 `hangil_rag.search_knowledge` (oneload-rag `v0.3.0`, in-memory search over its official documents) | `RAG_BACKEND=pgvector` switches it to pgvector; not used yet (in-memory scored better in R3's evaluation) |

`app/main.py` selects the OpenAI components at process startup. The API endpoints and response schemas do not change.

`hangil-rag` is pinned to a release tag by URL in `pyproject.toml` (the slim image has no git). To take a new R3 release, change the tag there and run `uv lock`.

## CI and deploy

`.github/workflows/ci.yml` runs lint and tests on every push, checks that the current oneload-data still loads with this code, and builds the `Dockerfile` to catch image breakage. On `main` the `deploy` job calls oneload-infra's shared deploy workflow on this repo's self-hosted runner on macserver. It deploys `main` of every repo: it builds the API image (with oneload_agent `main`) and the web image (oneload-app) on the server and runs them with Postgres as the compose project `hangil` (details and the Portainer dashboard in oneload-infra). oneload-app and oneload_agent call the same deploy from their own CI and runners.

CI and the deploy have no `../oneload_agent` checkout, so `scripts/pin_agent.sh` first rewrites the agent source to oneload_agent `main` by URL (never committed). Tests therefore always run against the agent that will be deployed. Run it before a local `docker build` too, then `git checkout pyproject.toml uv.lock`.

Redeploy by hand (after editing data, infra or a secret):

```sh
gh workflow run ci.yml -R team-onetruck/oneload-backend
```

### Settings and secrets

Server settings live only in **GitHub repository secrets** of this repo. When this repo deploys, the shared deploy workflow maps each one to an `APP_*` variable (`APP_OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}`), and the deploy script writes every non-empty one to `~/hangil-api.env` on macserver (mode 600, prefix removed, previous file replaced). Deploys started by oneload-app or oneload_agent leave the file alone. Never edit that file by hand or commit a value.

```sh
gh secret set OPENAI_API_KEY -R team-onetruck/oneload-backend   # prompts for the value
gh workflow run ci.yml -R team-onetruck/oneload-backend          # redeploy so the API picks it up
```

A new setting needs the secret plus its mapping in oneload-infra's `.github/workflows/deploy.yml`. Deleting a secret and redeploying from this repo removes it from the server.
