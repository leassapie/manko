"""Kurigram bot — handlers + orchestration."""

import asyncio
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pyrogram import Client, enums, filters
from pyrogram.types import Document, Message

from hstream_tg.config import Settings
from hstream_tg.downloader import (
    SeriesInfo,
    episode_url_to_series_url,
    ensure_dependencies,
    process_url,
    scrape_series_info,
)
from hstream_tg.thumb import (
    create_user_thumb,
    download_poster_thumb,
    resolve_doc_thumb,
    user_thumb_path,
)
from hstream_tg.uploader import (
    build_episode_caption,
    build_series_caption,
    media_destinations,
    make_progress_cb,
    make_upload_progress,
    progress_edit,
    send_document_no_reply,
    send_photo_no_reply,
)
from hstream_tg.utils import (
    episode_number_from_url,
    html_escape,
    human_size,
    rename_episode_file,
)

logger = logging.getLogger("hstream-tg")

URL_RE = re.compile(r"https?://(?:www\.)?hstream\.moe/hentai/[\w\-]+/?", re.I)


def create_app(settings: Settings) -> Client:
    return Client(
        settings.session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=str(Path(".").resolve()),
    )


def register_handlers(app: Client, settings: Settings) -> None:
    active_jobs: set[int] = set()
    executor = ThreadPoolExecutor(max_workers=settings.workers)

    def user_dir(user_id: int) -> Path:
        p = settings.download_root / str(user_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_cookies_path(user_id: int) -> Path:
        return settings.cookies_dir / f"{user_id}.txt"

    @app.on_message(filters.command("start"))
    async def start_cmd(client: Client, message: Message) -> None:
        text = (
            "👋 <b>HStream-TG</b> <i>(Kurigram / MTProto)</i>\n\n"
            "Send me one or more <b>hstream.moe episode links</b> and I will:\n"
            "• download the video (best quality)\n"
            "• try English .ass subtitles + remux to MKV\n"
            "• post <b>poster + series caption once</b> per hentai\n"
            "• leech each episode (up to ~2 GB via MTProto)\n\n"
            "<b>Commands</b>\n"
            "/start – this message\n"
            "/help – detailed help\n"
            "/cookies – upload cookies.txt\n"
            "/thumb – set custom leech thumbnail\n"
            "/status – jobs & disk usage\n"
            "/cancel – abort running job\n"
            "/clear – delete your temporary files\n\n"
            "⚠️ Only <b>single-episode</b> URLs\n"
            "(e.g. <code>https://hstream.moe/hentai/title-1</code>)"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command("help"))
    async def help_cmd(client: Client, message: Message) -> None:
        text = (
            "<b>How to use</b>\n\n"
            "1. (Optional) <code>/cookies</code> then send Netscape cookies.txt\n"
            "2. (Optional) <code>/thumb</code> then send a photo for custom leech thumb\n"
            "3. Paste one or more episode URLs.\n"
            "4. Large files upload via <b>Kurigram MTProto</b> "
            f"(soft limit <code>{settings.max_file_mb:.0f} MB</code>).\n"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command("status"))
    async def status_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        ud = user_dir(uid)
        total = sum(f.stat().st_size for f in ud.rglob("*") if f.is_file())
        files = list(ud.glob("*"))
        text = (
            f"👤 User <code>{uid}</code>\n"
            f"📂 Files: <b>{len(files)}</b>\n"
            f"💾 Size: <b>{human_size(total)}</b>\n"
            f"⚙️ Active jobs: <b>{len(active_jobs)}</b>\n"
            f"🍪 Cookies: {'✅' if user_cookies_path(uid).exists() else '❌'}\n"
            f"🖼 Thumb: {'✅' if user_thumb_path(uid).exists() else '❌'}\n"
            f"📦 Max upload: <b>{settings.max_file_mb:.0f} MB</b>\n"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command("cancel"))
    async def cancel_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        if uid in active_jobs:
            active_jobs.discard(uid)
            await message.reply("🛑 Job cancelled.")
        else:
            await message.reply("No active job to cancel.")

    @app.on_message(filters.command("clear"))
    async def clear_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        ud = user_dir(uid)
        removed = 0
        for f in ud.glob("*"):
            try:
                if f.is_file():
                    f.unlink()
                    removed += 1
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                    removed += 1
            except Exception:
                pass
        await message.reply(f"🧹 Cleared {removed} item(s).")

    @app.on_message(filters.command("cookies"))
    async def cookies_cmd(client: Client, message: Message) -> None:
        await message.reply(
            "🍪 Send a <b>Netscape cookies.txt</b> as a document.",
            parse_mode=enums.ParseMode.HTML,
        )
        (settings.cookies_dir / f".await_{message.from_user.id}").touch()

    @app.on_message(filters.command("thumb"))
    async def thumb_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        path = user_thumb_path(uid)
        if message.reply_to_message and message.reply_to_message.photo:
            dl = await message.reply_to_message.download()
            out = create_user_thumb(Path(dl), uid)
            Path(dl).unlink(missing_ok=True)
            if out:
                await message.reply("✅ Custom thumbnail saved (used for all uploads).")
            else:
                await message.reply("❌ Failed to save thumbnail (need ffmpeg).")
            return
        (settings.cookies_dir / f".await_thumb_{uid}").touch()
        extra = "\nCurrent: ✅ set" if path.exists() else "\nCurrent: ❌ none"
        await message.reply(
            "🖼 Send a <b>photo</b> now to set your leech thumbnail."
            f"{extra}\n"
            "Same idea as Aeon <code>/settings → thumbnail</code>.",
            parse_mode=enums.ParseMode.HTML,
        )

    @app.on_message(filters.photo)
    async def handle_photo(client: Client, message: Message) -> None:
        uid = message.from_user.id
        flag = settings.cookies_dir / f".await_thumb_{uid}"
        if not flag.exists():
            return
        dl = await message.download()
        out = create_user_thumb(Path(dl), uid)
        Path(dl).unlink(missing_ok=True)
        flag.unlink(missing_ok=True)
        if out:
            await message.reply("✅ Custom thumbnail saved.")
        else:
            await message.reply("❌ Failed to save thumbnail.")

    @app.on_message(filters.document)
    async def handle_document(client: Client, message: Message) -> None:
        uid = message.from_user.id
        flag = settings.cookies_dir / f".await_{uid}"
        if not flag.exists():
            return
        doc: Document = message.document
        name = (doc.file_name or "").lower()
        if not name.endswith((".txt", ".cookies")):
            await message.reply("Please send a .txt cookies file.")
            return
        dest = user_cookies_path(uid)
        await message.download(file_name=str(dest))
        flag.unlink(missing_ok=True)
        await message.reply(f"✅ Cookies saved ({human_size(dest.stat().st_size)}).")

    @app.on_message(
        filters.text
        & ~filters.command(["start", "help", "status", "cancel", "clear", "cookies", "thumb"])
    )
    async def handle_text(client: Client, message: Message) -> None:
        text = (message.text or "").strip()
        urls = URL_RE.findall(text)
        if not urls:
            await message.reply(
                "No valid hstream.moe episode URLs found.\n"
                "<code>https://hstream.moe/hentai/title-1</code>",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]
        uid = message.from_user.id
        if uid in active_jobs:
            await message.reply("⏳ You already have a job running.")
            return
        active_jobs.add(uid)
        try:
            await _process_urls(client, message, urls, settings, executor, active_jobs, user_dir, user_cookies_path)
        finally:
            active_jobs.discard(uid)


async def _process_urls(
    client: Client,
    message: Message,
    urls: list[str],
    settings: Settings,
    executor: ThreadPoolExecutor,
    active_jobs: set[int],
    user_dir_fn: object,
    user_cookies_path_fn: object,
) -> None:
    uid = message.from_user.id
    dest = settings.download_root / str(uid)
    dest.mkdir(parents=True, exist_ok=True)

    cookies = settings.cookies_dir / f"{uid}.txt"
    cookies_file = cookies if cookies.exists() else None

    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for u in urls:
        key = episode_url_to_series_url(u)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(u)

    total_eps = len(urls)
    total_series = len(order)
    status = await message.reply(
        f"🚀 Starting <b>{total_eps}</b> episode(s) across <b>{total_series}</b> series…\n"
        f"Cookies: {'✅' if cookies_file else '❌'}\n"
        f"Upload: <b>Kurigram</b> (up to {settings.max_file_mb:.0f} MB)",
        parse_mode=enums.ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    ep_global = 0

    for s_idx, series_key in enumerate(order, 1):
        series_urls = groups[series_key]

        def _scrape(_url: str = series_urls[0]) -> SeriesInfo:
            return scrape_series_info(_url, cookies_file=cookies_file)

        try:
            series_info: SeriesInfo = await loop.run_in_executor(executor, _scrape)
        except Exception as e:
            logger.warning("Series scrape failed for %s: %s", series_key, e)
            series_info = SeriesInfo(series_url=series_key)

        series_caption = build_series_caption(series_info, has_subs=True)
        title_label = series_info.title or series_key.rsplit("/", 1)[-1]
        anime_title = series_info.title or title_label
        dest_chats = media_destinations(settings, message.chat.id)

        series_thumb_dir = dest / "_thumbs" / series_key.rstrip("/").split("/")[-1]
        series_thumb: Path | None = None
        if series_info.poster_url:
            series_thumb = await loop.run_in_executor(
                executor,
                lambda: download_poster_thumb(series_info.poster_url, series_thumb_dir),
            )

        await progress_edit(
            status,
            f"📚 Series <b>[{s_idx}/{total_series}]</b> {html_escape(title_label)}\n"
            f"Episodes: <b>{len(series_urls)}</b>",
        )

        for chat_id in dest_chats:
            try:
                if series_info.poster_url:
                    await send_photo_no_reply(client, chat_id, series_info.poster_url, series_caption)
                else:
                    await client.send_message(
                        chat_id=chat_id,
                        text=series_caption,
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
            except Exception as e:
                logger.warning("Series info post failed to %s: %s", chat_id, e)

        for url in series_urls:
            ep_global += 1
            idx = ep_global

            if uid not in active_jobs:
                await progress_edit(status, "🛑 Job cancelled.")
                return

            progress_cb = make_progress_cb(status, idx, total_eps, url, settings, loop)

            try:
                final_path: Path = await loop.run_in_executor(
                    executor,
                    lambda _u=url: process_url(
                        _u, dest, cookies_file=cookies_file, progress=progress_cb
                    ),
                )
            except Exception as e:
                logger.exception("Failed %s", url)
                await progress_edit(
                    status, f"❌ <b>[{idx}/{total_eps}]</b> failed\n<code>{url}</code>\n{e}"
                )
                continue

            ep_num = episode_number_from_url(url)
            final_path = rename_episode_file(final_path, anime_title, ep_num)
            size_mb = final_path.stat().st_size / (1024 * 1024)
            has_subs = final_path.suffix.lower() == ".mkv"
            ep_caption = build_episode_caption(anime_title, ep_num, final_path, has_subs)

            await progress_edit(
                status,
                f"✅ <b>[{idx}/{total_eps}]</b> ready – uploading…\n"
                f"<code>{final_path.name}</code>\n"
                f"Size: {human_size(final_path.stat().st_size)}",
            )

            if size_mb > settings.max_file_mb:
                await message.reply(
                    f"📦 File too large ({size_mb:.1f} MB > {settings.max_file_mb:.0f} MB).\n{ep_caption}",
                    parse_mode=enums.ParseMode.HTML,
                )
                continue

            thumb_path = await loop.run_in_executor(
                executor,
                lambda: resolve_doc_thumb(final_path, uid, series_thumb, series_thumb_dir),
            )

            uploaded_ok = False
            for chat_id in dest_chats:
                upload_progress = make_upload_progress(status, idx, total_eps, final_path.name, settings)

                try:
                    await send_document_no_reply(
                        client,
                        chat_id,
                        str(final_path),
                        final_path.name,
                        ep_caption,
                        thumb_path,
                        progress=upload_progress,
                    )
                    uploaded_ok = True
                except Exception as e:
                    logger.exception("Upload failed to %s", chat_id)
                    await message.reply(
                        f"⚠️ Upload failed: {e}", parse_mode=enums.ParseMode.HTML
                    )

            if not settings.keep_files and uploaded_ok:
                try:
                    final_path.unlink(missing_ok=True)
                except Exception:
                    pass

    await progress_edit(
        status,
        f"🎉 Done! <b>{total_eps}</b> episode(s) / <b>{total_series}</b> series.\n"
        "/status or /clear when finished.",
    )
