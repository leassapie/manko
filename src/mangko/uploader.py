"""Upload helpers, progress tracking, and caption building."""

import asyncio
import time
from pathlib import Path

from pyrogram import Client, enums
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

from mangko.config import Settings
from mangko.downloader import SeriesInfo
from mangko.utils import html_escape, human_size, progress_bar, sys_stats_line


async def progress_edit(status: Message, text: str) -> None:
    try:
        await status.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await status.edit_text(text, parse_mode=enums.ParseMode.HTML)
        except RPCError:
            pass
    except RPCError:
        pass


async def send_photo_no_reply(client: Client, chat_id: int | str, photo: str, caption: str = "") -> None:
    await client.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=caption or None,
        parse_mode=enums.ParseMode.HTML,
    )


async def send_document_no_reply(
    client: Client,
    chat_id: int | str,
    document: str,
    file_name: str,
    caption: str,
    thumb: str | None = None,
    progress: object = None,
) -> None:
    kwargs: dict = dict(
        chat_id=chat_id,
        document=document,
        file_name=file_name,
        caption=caption,
        parse_mode=enums.ParseMode.HTML,
    )
    if thumb:
        kwargs["thumb"] = thumb
    if progress is not None:
        kwargs["progress"] = progress
    try:
        await client.send_document(**kwargs)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.send_document(**kwargs)
    except Exception:
        kwargs.pop("thumb", None)
        try:
            await client.send_document(**kwargs)
        except Exception:
            kwargs.pop("progress", None)
            await client.send_document(**kwargs)


def media_destinations(settings: Settings, fallback_chat_id: int | str) -> list[int | str]:
    dests = []
    primary = settings.upload_chat if settings.upload_chat is not None else fallback_chat_id
    dests.append(primary)
    if settings.dump_chat is not None and settings.dump_chat != primary:
        dests.append(settings.dump_chat)
    return dests


def build_series_caption(info: SeriesInfo, has_subs: bool = True) -> str:
    title = info.title or "Unknown"
    lines = [f"<b>📖 {html_escape(title)}</b>"]
    if info.title_jp:
        lines.append(f"<i>{html_escape(info.title_jp)}</i>")
    lines.append("")
    meta: list[str] = []
    if info.year:
        meta.append(f"📅 Year: <code>{html_escape(info.year)}</code>")
    if info.status:
        meta.append(f"📊 Status: <b>{html_escape(info.status)}</b>")
    if info.episodes:
        meta.append(f"📑 Episodes: <code>{info.episodes}</code>")
    if info.tags:
        tags = ", ".join(info.tags[:8])
        meta.append(f"🏷️ Tags: {html_escape(tags)}")
    if info.studio:
        meta.append(f"🌐 Studio: {html_escape(info.studio)}")
    meta.append(
        "🗣 Language: Japanese + Eng subs" if has_subs else "🗣 Language: Japanese"
    )
    if meta:
        lines += ["", "\n".join(meta)]
    text = "\n".join(lines)
    return text[:1020] + "…" if len(text) > 1024 else text


def build_episode_caption(anime_title: str, ep_num: str, final_path: Path, has_subs: bool) -> str:
    title = html_escape(anime_title) if anime_title else html_escape(final_path.stem)
    lines = [
        f"📖 <b>{title}</b> — Episode {ep_num}",
        f"💬 Subtitle: {'English ✅' if has_subs else '❌ None'}",
        f"📏 {human_size(final_path.stat().st_size)}",
    ]
    return "\n".join(lines)


def make_progress_cb(
    status: Message,
    idx: int,
    total: int,
    url: str,
    settings: Settings,
    loop: asyncio.AbstractEventLoop,
) -> object:
    def _cb(msg: str) -> None:
        text = (
            f"📥 <b>[{idx}/{total}]</b> <code>{html_escape(url.split('/')[-1])}</code>\n"
            f"{msg}"
        )
        asyncio.run_coroutine_threadsafe(progress_edit(status, text), loop)

    return _cb


def make_upload_progress(
    status: Message,
    idx: int,
    total: int,
    name: str,
    settings: Settings,
) -> object:
    last_up = [0.0]

    async def _progress(current: int, total_bytes: int) -> None:
        now = time.time()
        if total_bytes and now - last_up[0] < 1.2 and current < total_bytes:
            return
        last_up[0] = now
        pct = (100.0 * current / total_bytes) if total_bytes else 0.0
        bar = progress_bar(pct)
        text = (
            f"📤 <b>[{idx}/{total}] Upload</b>\n"
            f"<code>{html_escape(name)}</code>\n"
            f"{bar} <b>{pct:.1f}%</b>\n"
            f"📤 {human_size(current)} / {human_size(total_bytes) if total_bytes else '—'}\n"
            f"<i>{sys_stats_line(settings.download_root)}</i>"
        )
        await progress_edit(status, text)

    return _progress
