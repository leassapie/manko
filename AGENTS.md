# Mangko

Telegram bot for [hstream.moe](https://hstream.moe) — downloads episodes via yt-dlp, resolves English `.ass` subtitles, remuxes to MKV via ffmpeg, and uploads up to ~2 GB through MTProto (Kurigram). No Bot API size limit.

## Tech Stack

- **Python 3.14** (PEP 649 deferred annotations, PEP 758 bracketless except)
- **Kurigram** (`pip install kurigram`) — actively maintained Pyrogram fork, drop-in compatible
- **pydantic-settings** — typed env config with validation
- **uv** — package manager + build backend (replaces pip + requirements.txt)
- **yt-dlp** + **hanime-plugin** — video downloading
- **ffmpeg** + **aria2c** — system deps (not pip-installable)

## System prerequisites

```bash
apt-get install -y ffmpeg aria2   # Debian/Ubuntu
brew install ffmpeg aria2          # macOS
```

`deno` is optional (some hanime-plugin features). Installed in Dockerfile.

## Run locally

```bash
uv sync                            # install deps
cp .env.example .env               # fill BOT_TOKEN, API_ID, API_HASH
uv run python -m mangko        # start bot
```

Or Docker:
```bash
docker build -t mangko .
docker run -d --env-file .env mangko
```

## Architecture

```
src/mangko/
├── __init__.py      # version
├── __main__.py      # entry: python -m mangko
├── config.py        # pydantic-settings (typed env)
├── bot.py           # Kurigram app + handlers (thin)
├── downloader.py    # yt-dlp download + subtitle resolve + remux
├── thumb.py         # thumbnail resolution (user custom / video frame)
├── uploader.py      # upload helpers, progress, captions
├── updater.py       # auto-updater from upstream repo
├── utils.py         # shared helpers (human_size, html_escape, etc.)
└── py.typed         # PEP 561 marker
```

Import graph: `__main__` → `bot` → `downloader`, `uploader`, `thumb`, `utils` → `config`

## Configuration

All env vars via pydantic-settings. Required: `BOT_TOKEN`, `API_ID`, `API_HASH`. See `.env.example`.

Key defaults: `MAX_FILE_MB=2000`, `WORKERS=2`, `KEEP_FILES=false`.

## Gotchas

- **Per-user job lock:** Only one download job per user at a time. `/cancel` to abort.
- **`ensure_dependencies()` runs pip install at runtime** — upgrades yt-dlp/requests/hanime-plugin.
- **Subtitle resolution is best-effort.** CDN hosts rotate; resolution may silently fail.
- **Session files preserved across resets:** `.env`, `*.session`, `*.session-journal` survive auto-update.
- **`updater.py` does `git fetch + reset --hard`** on every restart.
- **Per-user file isolation:** `downloads/{user_id}/` directories. Deleted after upload unless `KEEP_FILES=true`.

## Deploy

Supports: Docker standalone, Google Colab.

Auto-update: pulls `UPSTREAM_REPO` on every restart via `start.sh` → `updater.py`.

## Dev commands

```bash
uv run ruff check src/              # lint
uv run ruff format src/             # format
uv run mypy src/                    # type check
PYTHONPATH=src uv run pytest -q     # test
```

## No automated verification

CI runs ruff + mypy + pytest on push/PR via `.github/workflows/ci.yml`.
