#!/bin/bash
# Always pull latest from UPSTREAM_REPO then start bot.
set -euo pipefail

echo "[start] launching HStream-TG…"

find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

exec uv run python -m hstream_tg
