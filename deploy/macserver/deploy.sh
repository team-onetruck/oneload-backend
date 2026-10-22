#!/usr/bin/env bash
# Deploys the checked-out commit to macserver. Run by the self-hosted runner (.github/workflows/ci.yml);
# safe to run by hand from a checkout on the server too.
#
#   ~/hangil-api        application code + .venv
#   ~/hangil-api.env    server-only settings and secrets (e.g. OPENAI_API_KEY), never in git
#   ~/hangil-demo/      web client build (oneload-app) + Caddyfile, served on 127.0.0.1:4173
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
APP="$HOME/hangil-api"
ENV_FILE="$HOME/hangil-api.env"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"

rsync -a --delete --exclude .git --exclude .venv --exclude .env "$SRC/" "$APP/"
touch "$ENV_FILE" && chmod 600 "$ENV_FILE"
cd "$APP"

docker compose -p hangil up -d --wait db
uv sync --frozen --no-dev
uv run --frozen --no-dev --env-file "$ENV_FILE" alembic upgrade head

mkdir -p "$HOME/hangil-demo" "$AGENTS"
cp deploy/macserver/Caddyfile "$HOME/hangil-demo/Caddyfile"

reload_agent() {
  local label=$1
  sed "s|__HOME__|$HOME|g" "deploy/macserver/launchd/$label.plist" > "$AGENTS/$label.plist"
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$AGENTS/$label.plist"
}
reload_agent com.onetruck.hangil-api
# A Caddy started by hand (outside launchd) would keep the port, so stop whatever owns the admin API first.
launchctl bootout "$DOMAIN/com.onetruck.hangil-caddy" 2>/dev/null || true
caddy stop >/dev/null 2>&1 || true
reload_agent com.onetruck.hangil-caddy

for _ in $(seq 30); do
  if curl -fsS http://127.0.0.1:4173/api/v1/health; then
    echo && echo "Deployed $(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    exit 0
  fi
  sleep 1
done
echo "API did not become healthy" >&2
tail -50 "$HOME/Library/Logs/hangil-api.log" >&2
exit 1
