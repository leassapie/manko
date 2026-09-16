# Mangko

Telegram bot for **[hstream.moe](https://hstream.moe)** – bulk download, English subtitle remux, and leech.

Built on [Hstream-Extractor](https://github.com/zenin-373/Hstream-Extractor) logic (**yt-dlp** + **hanime-plugin** + **ffmpeg**).  
Uploads use **[wzgram](https://github.com/rjriajul/wzgram)** (Pyrogram MTProto) so files up to ~**2 GB** can be sent (not the old Bot API ~50 MB limit).

## Features

- Send one or more **single-episode** URLs → download + best-effort English `.ass` + MKV remux
- **Series card once per hentai**: poster + rich caption (Manga-DL style), then all episodes of that series
- Multi-series batches: next series gets its own poster/caption, then its files
- Subtitle resolve: page scrape → player API → known CDN hosts
- Live progress status messages
- `/cookies` – per-user Netscape `cookies.txt` for login-walled titles
- Per-user download folders + `/clear` + `/status`
- Heavy work in a thread pool (bot stays responsive)
- Soft upload cap via `MAX_FILE_MB` (default **2000**)
- Docker-ready

## Limitations

- **Only individual episode links**  
  `https://hstream.moe/hentai/title-1` ✅  
  Series / playlist pages are **not** expanded ❌
- Subtitle CDNs rotate – subs are best-effort
- Needs `API_ID` + `API_HASH` from [my.telegram.org](https://my.telegram.org) (wzgram / MTProto)

## Caption layout (once per series)

```
📖 Title
日本語タイトル

📅 Year: 2005
📊 Status: Completed
📑 Total Episodes: 2
🏷️ Tags: …
🌐 Studio: …
🗣 Language: Japanese + Eng subs
```

Then each episode file is leeched with a short caption (`Episode N` + filename + size).

## Quick start (local)

### 1. Requirements

- Python 3.10+
- `ffmpeg` and `aria2c` in PATH (recommended)
- Deno (optional, some hanime-plugin features)

```bash
# Debian / Ubuntu / WSL
sudo apt update && sudo apt install -y ffmpeg aria2

# macOS
brew install ffmpeg aria2
```

### 2. Clone & install

```bash
git clone https://github.com/leassapie/manko.git
cd manko
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Required | Notes |
|----------|----------|--------|
| `BOT_TOKEN` | yes | From [@BotFather](https://t.me/BotFather) |
| `API_ID` | yes | [my.telegram.org](https://my.telegram.org) → API development tools |
| `API_HASH` | yes | same as above |
| `MAX_FILE_MB` | no | default `2000` |
| `OWNER_ID` | no | your Telegram user id |
| `WORKERS` | no | concurrent downloads (default `2`) |

### 4. Run

```bash
python bot.py
```

Open Telegram → `/start` → paste one or more episode links.

## Google Colab

```python
# 1) system deps
!apt-get update -qq && apt-get install -y -qq ffmpeg aria2 curl
# optional deno
!command -v deno >/dev/null || (curl -fsSL https://deno.land/install.sh | sh)
import os
os.environ["PATH"] = os.path.expanduser("~/.deno/bin") + os.pathsep + os.environ.get("PATH", "")

# 2) clone + pip
%cd /content
!rm -rf manko
!git clone --depth 1 https://github.com/leassapie/manko.git
%cd manko
!pip install -q -r requirements.txt

# 3) .env  (fill these)
from pathlib import Path
Path(".env").write_text("""
BOT_TOKEN=your_bot_token
API_ID=12345678
API_HASH=your_api_hash
MAX_FILE_MB=2000
DOWNLOAD_ROOT=downloads
COOKIES_DIR=user_cookies
KEEP_FILES=false
WORKERS=2
SESSION_NAME=hstream_tg
""".strip() + "\n")

# 4) run (keep cell running)
!python -u bot.py
```

## Docker

```bash
docker build -t mangko .

docker run -d \
  --name mangko \
  -e BOT_TOKEN=your_token \
  -e API_ID=your_api_id \
  -e API_HASH=your_api_hash \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/user_cookies:/app/user_cookies \
  mangko
```

Or:

```bash
docker run -d --name mangko --env-file .env \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/user_cookies:/app/user_cookies \
  mangko
```

## Bot commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome + short guide |
| `/help` | Detailed usage |
| `/cookies` | Then upload Netscape `cookies.txt` |
| `/status` | Files, size, active jobs, cookies |
| `/clear` | Delete your temporary files |

Paste episode URLs in a normal message (one or many). Same series → one poster; different series → new poster each.

## Cookies (restricted titles)

1. Log in on hstream.moe in your browser.
2. Export cookies (Netscape format) with a cookies.txt extension.
3. In the bot: `/cookies` → send the `.txt` as a document.
4. Stored only for your Telegram user id.

Never commit real cookies.

## Environment variables

See `.env.example`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `BOT_TOKEN` | *required* | From @BotFather |
| `API_ID` | *required* | my.telegram.org |
| `API_HASH` | *required* | my.telegram.org |
| `OWNER_ID` | `0` | Your Telegram id (optional) |
| `MAX_FILE_MB` | `2000` | Soft max upload size (MB) |
| `DOWNLOAD_ROOT` | `downloads` | Download directory |
| `COOKIES_DIR` | `user_cookies` | Per-user cookies |
| `KEEP_FILES` | `false` | Keep files after send |
| `WORKERS` | `2` | Concurrent download threads |
| `SESSION_NAME` | `hstream_tg` | wzgram session name |

## Architecture

```
bot.py          ← wzgram (Pyrogram) handlers, series grouping, upload
extractor.py    ← download / subtitle resolve / remux (sync, thread-safe)
```

Heavy work runs in a `ThreadPoolExecutor` so the event loop stays free.

## Credits

- MTProto client: [wzgram](https://github.com/rjriajul/wzgram) (Pyrogram-compatible)
- Site extraction: [hanime-plugin](https://github.com/cynthia2006/hanime-plugin) by cynthia2006
- Original CLI: [Hstream-Extractor](https://github.com/zenin-373/Hstream-Extractor)

## License

MIT

## Disclaimer

Personal / educational / archival use only. Respect the site’s Terms of Service and copyright laws.
