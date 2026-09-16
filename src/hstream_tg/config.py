"""Typed configuration via pydantic-settings."""

from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Required ────────────────────────────────────────
    bot_token: str = Field(..., description="Telegram bot token from @BotFather")
    api_id: int = Field(..., description="Telegram API ID from my.telegram.org")
    api_hash: str = Field(..., description="Telegram API hash from my.telegram.org")

    # ── Optional ────────────────────────────────────────
    owner_id: int = 0
    max_file_mb: float = 2000.0
    download_root: Path = Path("downloads")
    cookies_dir: Path = Path("user_cookies")
    thumb_dir: Path = Path("thumbnails")
    history_dir: Path = Path("download_history")
    keep_files: bool = False
    workers: int = 2
    session_name: str = "hstream_tg"
    upload_channel: str | None = None
    dump_channel: str | None = None

    # ── Quality & Subtitle ─────────────────────────────
    default_quality: str = "best"
    default_subtitle: str = "en"

    # ── Auto-delete ────────────────────────────────────
    auto_delete_days: int = 0

    # ── Naming template ────────────────────────────────
    naming_template: str = "{title} - Episode {episode}"

    # ── Premium queue ──────────────────────────────────
    max_concurrent_per_user: int = 1
    max_daily_downloads: int = 50

    # ── Notification ───────────────────────────────────
    notify_dm: bool = True

    # ── Auto-update ─────────────────────────────────────
    upstream_repo: str = "https://github.com/leassapie/manko.git"
    upstream_branch: str = "main"

    # ── Computed ────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def upload_chat(self) -> int | str | None:
        return _parse_chat_id(self.upload_channel)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dump_chat(self) -> int | str | None:
        return _parse_chat_id(self.dump_channel)

    def ensure_dirs(self) -> None:
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)


def _parse_chat_id(raw: str | None) -> int | str | None:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    if s.startswith("@"):
        return s
    try:
        return int(s)
    except ValueError:
        return s


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
