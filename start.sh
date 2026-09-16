#!/bin/bash
# Always pull latest from UPSTREAM_REPO then start bot.
set -euo pipefail

echo "[start] launching HStream-TG…"

exec uv run python -m hstream_tg
