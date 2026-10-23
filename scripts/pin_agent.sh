#!/usr/bin/env bash
# Pins oneload-agent to one oneload_agent commit by URL, for places that have no ../oneload_agent
# checkout (CI, the Docker build, the deploy). Changes pyproject.toml and uv.lock in this checkout;
# never commit the result, local development stays on the sibling checkout.
#
#   usage: scripts/pin_agent.sh [commit]      (default: current oneload_agent main)
set -euo pipefail

repo=https://github.com/team-onetruck/oneload_agent
commit="${1:-$(git ls-remote "$repo" refs/heads/main | cut -f1)}"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "not a full commit id: '$commit'" >&2; exit 2; }

cd "$(dirname "$0")/.."
uv add --no-sync --quiet "oneload-agent @ $repo/archive/$commit.tar.gz"
echo "oneload-agent pinned to ${commit:0:7}"
