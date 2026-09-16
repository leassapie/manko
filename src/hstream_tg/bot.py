"""Kurigram bot — handlers + orchestration."""

import asyncio
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from hstream_tg.config import Settings
from hstream_tg.downloader import (
    SeriesInfo,
    cleanup_old_files,
    episode_url_to_series_url,
    ensure_dependencies,
    get_download_history,
    get_user_stats,
    process_url,
    save_download_history,
    scrape_episode_list,
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
    progress_bar,
    rename_episode_file,
    sys_stats_line,
)

logger = logging.getLogger("mangko")

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
    user_quality: dict[int, str] = {}
    user_subtitle: dict[int, str] = {}
    executor = ThreadPoolExecutor(max_workers=settings.workers)

    def user_dir(user_id: int) -> Path:
        p = settings.download_root / str(user_id)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_cookies_path(user_id: int) -> Path:
        return settings.cookies_dir / f"{user_id}.txt"

    def get_quality(uid: int) -> str:
        return user_quality.get(uid, settings.default_quality)

    def get_subtitle(uid: int) -> str:
        return user_subtitle.get(uid, settings.default_subtitle)

    @app.on_message(filters.command("start"))
    async def start_cmd(client: Client, message: Message) -> None:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📖 Tutorial", callback_data="tutorial"),
                    InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
                ],
                [
                    InlineKeyboardButton("📊 Stats", callback_data="stats"),
                    InlineKeyboardButton("📜 History", callback_data="history"),
                ],
            ]
        )
        text = (
            "👋 <b>Mangko</b>\n\n"
            "Download episode hstream.moe langsung ke Telegram.\n"
            "Quality terbaik, subtitle Inggris, remux MKV.\n\n"
            "⚡ <b>Quick Start</b>\n"
            "1. /cookies — upload Netscape cookies.txt\n"
            "2. Kirim link episode\n"
            "3. Bot download & upload otomatis\n\n"
            "📚 <b>Commands</b>\n"
            "/start — mulai\n"
            "/help — bantuan\n"
            "/cookies — set cookies\n"
            "/thumb — set thumbnail\n"
            "/quality — pilih resolusi\n"
            "/subtitle — pilih bahasa subtitle\n"
            "/search — cari anime\n"
            "/batch — download semua episode\n"
            "/status — cek status\n"
            "/cancel — batalkan job\n"
            "/clear — hapus file\n\n"
            "⚠️ Kirim link <b>single episode</b> saja\n"
            "contoh: <code>https://hstream.moe/hentai/title-1</code>"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^tutorial$"))
    async def tutorial_cb(client: Client, callback: CallbackQuery) -> None:
        text = (
            "📖 <b>Tutorial</b>\n\n"
            "<b>1. Setup Cookies</b>\n"
            "Ketik <code>/cookies</code> lalu kirim file cookies.txt\n"
            "dari browser (format Netscape).\n\n"
            "<b>2. Pilih Quality</b>\n"
            "Ketik <code>/quality</code> untuk pilih resolusi.\n"
            "Default: best (otomatis terbaik).\n\n"
            "<b>3. Pilih Subtitle</b>\n"
            "Ketik <code>/subtitle</code> untuk pilih bahasa.\n"
            "Default: English.\n\n"
            "<b>4. Kirim Link</b>\n"
            "Paste link episode hstream.moe.\n"
            "Contoh: <code>https://hstream.moe/hentai/title-1</code>\n\n"
            "<b>5. Tunggu</b>\n"
            "Bot akan download, remux, lalu upload.\n"
            "Proses biasanya 1-5 menit per episode.\n\n"
            "<b>6. Selesai</b>\n"
            "File akan muncul di chat ini.\n"
            "Ketik <code>/status</code> untuk cek progress."
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex("^settings$"))
    async def settings_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        cookies_ok = user_cookies_path(uid).exists()
        thumb_ok = user_thumb_path(uid).exists()
        quality = get_quality(uid)
        subtitle = get_subtitle(uid)
        text = (
            "⚙️ <b>Settings</b>\n\n"
            f"🍪 Cookies: {'✅ aktif' if cookies_ok else '❌ belum set'}\n"
            f"🖼 Thumbnail: {'✅ custom' if thumb_ok else '❌ default'}\n"
            f"🎬 Quality: <code>{quality}</code>\n"
            f"💬 Subtitle: <code>{subtitle}</code>\n"
            f"📦 Max upload: <code>{settings.max_file_mb:.0f} MB</code>\n"
            f"🗑 Auto-delete: <code>{settings.auto_delete_days} hari</code>\n\n"
            "Ubah dengan perintah:\n"
            "/cookies — upload cookies baru\n"
            "/thumb — set custom thumbnail\n"
            "/quality — pilih resolusi\n"
            "/subtitle — pilih bahasa subtitle"
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex("^stats$"))
    async def stats_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        stats = get_user_stats(settings.history_dir, uid)
        text = (
            f"📊 <b>Stats</b> — User <code>{uid}</code>\n\n"
            f"📥 Total download: <b>{stats['total_downloads']}</b>\n"
            f"💾 Total size: <b>{human_size(stats['total_size'])}</b>\n"
        )
        if stats["formats"]:
            text += "\n📊 <b>Quality Distribution</b>\n"
            for q, count in stats["formats"].items():
                text += f"• {q}: <b>{count}</b>\n"
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_callback_query(filters.regex("^history$"))
    async def history_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        history = get_download_history(settings.history_dir, uid, limit=5)
        if not history:
            text = "📜 <b>History</b>\n\nBelum ada download."
        else:
            text = "📜 <b>History</b> (5 terakhir)\n\n"
            for i, h in enumerate(history, 1):
                filename = h.get("filename", "unknown")
                size = human_size(h.get("size", 0))
                quality = h.get("quality", "?")
                text += f"{i}. <code>{filename}</code>\n   📏 {size} • 🎬 {quality}\n"
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command("help"))
    async def help_cmd(client: Client, message: Message) -> None:
        text = (
            "❓ <b>Bantuan</b>\n\n"
            "<b>Cara Pakai</b>\n"
            "1. <code>/cookies</code> lalu kirim cookies.txt\n"
            "2. (Optional) <code>/thumb</code> lalu kirim foto\n"
            "3. (Optional) <code>/quality</code> pilih resolusi\n"
            "4. (Optional) <code>/subtitle</code> pilih bahasa\n"
            "5. Kirim link episode hstream.moe\n"
            f"6. File di-upload via MTProto (max <code>{settings.max_file_mb:.0f} MB</code>)\n\n"
            "<b>Commands</b>\n"
            "/quality — pilih resolusi (best/1080p/720p/480p)\n"
            "/subtitle — pilih bahasa (en/id/jp)\n"
            "/search — cari anime dari judul\n"
            "/batch — download semua episode series\n"
            "/history — riwayat download\n"
            "/stats — statistik download\n\n"
            "<b>Troubleshooting</b>\n"
            "• Download gagal? Coba /cookies dulu\n"
            "• File terlalu besar? Bot akan beri tahu\n"
            "• Ingin batal? Ketik /cancel"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)

    @app.on_message(filters.command("status"))
    async def status_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        ud = user_dir(uid)
        total = sum(f.stat().st_size for f in ud.rglob("*") if f.is_file())
        files = list(ud.glob("*"))
        cookies_ok = user_cookies_path(uid).exists()
        thumb_ok = user_thumb_path(uid).exists()
        jobs = len(active_jobs)
        quality = get_quality(uid)
        subtitle = get_subtitle(uid)

        text = (
            f"📊 <b>Status</b> — User <code>{uid}</code>\n\n"
            f"📂 Files: <b>{len(files)}</b>\n"
            f"💾 Size: <b>{human_size(total)}</b>\n"
            f"⚙️ Jobs: <b>{jobs} active</b>\n"
            f"🍪 Cookies: {'✅ ada' if cookies_ok else '❌ belum'}\n"
            f"🖼 Thumbnail: {'✅ set' if thumb_ok else '❌ default'}\n"
            f"🎬 Quality: <code>{quality}</code>\n"
            f"💬 Subtitle: <code>{subtitle}</code>\n"
            f"📦 Max upload: <code>{settings.max_file_mb:.0f} MB</code>"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
                    InlineKeyboardButton("🧹 Clear", callback_data="status_clear"),
                ],
            ]
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^status_refresh$"))
    async def status_refresh_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        ud = user_dir(uid)
        total = sum(f.stat().st_size for f in ud.rglob("*") if f.is_file())
        files = list(ud.glob("*"))
        cookies_ok = user_cookies_path(uid).exists()
        thumb_ok = user_thumb_path(uid).exists()
        jobs = len(active_jobs)
        quality = get_quality(uid)
        subtitle = get_subtitle(uid)

        text = (
            f"📊 <b>Status</b> — User <code>{uid}</code>\n\n"
            f"📂 Files: <b>{len(files)}</b>\n"
            f"💾 Size: <b>{human_size(total)}</b>\n"
            f"⚙️ Jobs: <b>{jobs} active</b>\n"
            f"🍪 Cookies: {'✅ ada' if cookies_ok else '❌ belum'}\n"
            f"🖼 Thumbnail: {'✅ set' if thumb_ok else '❌ default'}\n"
            f"🎬 Quality: <code>{quality}</code>\n"
            f"💬 Subtitle: <code>{subtitle}</code>\n"
            f"📦 Max upload: <code>{settings.max_file_mb:.0f} MB</code>"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
                    InlineKeyboardButton("🧹 Clear", callback_data="status_clear"),
                ],
            ]
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^status_clear$"))
    async def status_clear_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
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
        await callback.message.edit_text(
            f"🧹 Berhasil hapus {removed} item(s).",
            parse_mode=enums.ParseMode.HTML,
        )

    @app.on_message(filters.command("quality"))
    async def quality_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        current = get_quality(uid)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌟 Best", callback_data="q_best"),
                    InlineKeyboardButton("🎬 2160p", callback_data="q_2160p"),
                ],
                [
                    InlineKeyboardButton("📹 1080p", callback_data="q_1080p"),
                    InlineKeyboardButton("🖥 720p", callback_data="q_720p"),
                ],
                [
                    InlineKeyboardButton("📱 480p", callback_data="q_480p"),
                    InlineKeyboardButton("📞 360p", callback_data="q_360p"),
                ],
            ]
        )
        text = (
            f"🎬 <b>Quality Selection</b>\n\n"
            f"Sekarang: <code>{current}</code>\n\n"
            "Pilih resolusi untuk download:\n"
            "• <b>Best</b> — Otomatis terbaik\n"
            "• <b>2160p</b> — 4K Ultra HD\n"
            "• <b>1080p</b> — Full HD\n"
            "• <b>720p</b> — HD\n"
            "• <b>480p</b> — SD\n"
            "• <b>360p</b> — Low"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^q_(best|2160p|1080p|720p|480p|360p)$"))
    async def quality_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        quality = callback.data.split("_", 1)[1]
        user_quality[uid] = quality
        await callback.message.edit_text(
            f"✅ Quality diubah ke <code>{quality}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    @app.on_message(filters.command("subtitle"))
    async def subtitle_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        current = get_subtitle(uid)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🇬🇧 English", callback_data="sub_en"),
                    InlineKeyboardButton("🇮🇩 Indonesia", callback_data="sub_id"),
                ],
                [
                    InlineKeyboardButton("🇯🇵 Japanese", callback_data="sub_jp"),
                ],
            ]
        )
        text = (
            f"💬 <b>Subtitle Selection</b>\n\n"
            f"Sekarang: <code>{current}</code>\n\n"
            "Pilih bahasa subtitle:\n"
            "• <b>English</b> — Subtitle Inggris\n"
            "• <b>Indonesia</b> — Subtitle Indonesia\n"
            "• <b>Japanese</b> — Subtitle Jepang"
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^sub_(en|id|jp)$"))
    async def subtitle_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        subtitle = callback.data.split("_", 1)[1]
        user_subtitle[uid] = subtitle
        await callback.message.edit_text(
            f"✅ Subtitle diubah ke <code>{subtitle}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    @app.on_message(filters.command("search"))
    async def search_cmd(client: Client, message: Message) -> None:
        text = (
            "🔍 <b>Search Anime</b>\n\n"
            "Kirim judul anime yang ingin dicari.\n"
            "Contoh: <code>Yuki</code>\n\n"
            "Bot akan mencari di hstream.moe."
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)
        (settings.cookies_dir / f".await_search_{message.from_user.id}").touch()

    @app.on_message(filters.command("batch"))
    async def batch_cmd(client: Client, message: Message) -> None:
        text = (
            "📦 <b>Batch Download</b>\n\n"
            "Kirim link series untuk download semua episode.\n"
            "Contoh: <code>https://hstream.moe/hentai/title</code>\n\n"
            "⚠️ Tanpa angka episode di akhir URL."
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)
        (settings.cookies_dir / f".await_batch_{message.from_user.id}").touch()

    @app.on_message(filters.command("cancel"))
    async def cancel_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        if uid in active_jobs:
            active_jobs.discard(uid)
            await message.reply("🛑 Job dibatalkan.")
        else:
            await message.reply("Tidak ada job yang berjalan.")

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
        await message.reply(f"🧹 Berhasil hapus {removed} item(s).")

    @app.on_message(filters.command("cookies"))
    async def cookies_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        has_cookies = user_cookies_path(uid).exists()
        status = "✅ Sudah ada" if has_cookies else "❌ Belum ada"
        text = (
            "🍪 <b>Setup Cookies</b>\n\n"
            f"Status: {status}\n\n"
            "Kirim file <code>cookies.txt</code> (Netscape format)\n"
            "sebagai document/folder.\n\n"
            "💡 Cookies dibutuhkan untuk download.\n"
            "Tanpa cookies, beberapa video mungkin gagal."
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)
        (settings.cookies_dir / f".await_{uid}").touch()

    @app.on_message(filters.command("thumb"))
    async def thumb_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        path = user_thumb_path(uid)
        if message.reply_to_message and message.reply_to_message.photo:
            dl = await message.reply_to_message.download()
            out = create_user_thumb(Path(dl), uid)
            Path(dl).unlink(missing_ok=True)
            if out:
                await message.reply("✅ Thumbnail tersimpan!")
            else:
                await message.reply("❌ Gagal simpan thumbnail (butuh ffmpeg).")
            return
        (settings.cookies_dir / f".await_thumb_{uid}").touch()
        status = "✅ Sudah ada" if path.exists() else "❌ Belum ada"
        text = (
            "🖼 <b>Setup Thumbnail</b>\n\n"
            f"Status: {status}\n\n"
            "Kirim foto untuk dijadikan thumbnail upload.\n"
            "Thumbnail digunakan untuk semua upload.\n\n"
            "💡 Reply foto dengan /thumb juga bisa."
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML)

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
            await message.reply("✅ Thumbnail tersimpan!")
        else:
            await message.reply("❌ Gagal simpan thumbnail.")

    @app.on_message(filters.document)
    async def handle_document(client: Client, message: Message) -> None:
        uid = message.from_user.id
        flag = settings.cookies_dir / f".await_{uid}"
        if not flag.exists():
            return
        doc: Document = message.document
        name = (doc.file_name or "").lower()
        if not name.endswith((".txt", ".cookies")):
            await message.reply(
                "❌ File tidak valid.\n"
                "Kirim file <code>cookies.txt</code> (Netscape format).",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        dest = user_cookies_path(uid)
        await message.download(file_name=str(dest))
        flag.unlink(missing_ok=True)
        await message.reply(
            f"✅ Cookies tersimpan!\n"
            f"📏 Size: <code>{human_size(dest.stat().st_size)}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    @app.on_message(filters.text & ~filters.command([
        "start", "help", "status", "cancel", "clear",
        "cookies", "thumb", "quality", "subtitle", "search", "batch",
    ]))
    async def handle_text(client: Client, message: Message) -> None:
        uid = message.from_user.id
        text = (message.text or "").strip()

        if (settings.cookies_dir / f".await_search_{uid}").exists():
            (settings.cookies_dir / f".await_search_{uid}").unlink(missing_ok=True)
            await _handle_search(client, message, text, settings, executor)
            return

        if (settings.cookies_dir / f".await_batch_{uid}").exists():
            (settings.cookies_dir / f".await_batch_{uid}").unlink(missing_ok=True)
            await _handle_batch(client, message, text, settings, executor, active_jobs, user_dir, user_cookies_path, get_quality, get_subtitle)
            return

        urls = URL_RE.findall(text)
        if not urls:
            text = (
                "🔍 Link tidak ditemukan.\n\n"
                "Format yang benar:\n"
                "<code>https://hstream.moe/hentai/title-1</code>\n\n"
                "💡 Kirim satu link per line untuk multiple episodes."
            )
            await message.reply(text, parse_mode=enums.ParseMode.HTML)
            return
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]
        if uid in active_jobs:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🛑 Batalkan", callback_data="cancel_job")]]
            )
            await message.reply(
                "⏳ Kamu sudah punya job yang berjalan.\nTunggu selesai atau batalkan.",
                reply_markup=kb,
            )
            return

        preview_urls = urls[:5]
        preview_text = "🔍 <b>Preview</b>\n\n"
        for i, u in enumerate(preview_urls, 1):
            slug = u.rstrip("/").split("/")[-1]
            preview_text += f"{i}. <code>{slug}</code>\n"
        if len(urls) > 5:
            preview_text += f"... dan {len(urls) - 5} lagi\n"
        preview_text += f"\nTotal: <b>{len(urls)}</b> episode"

        quality = get_quality(uid)
        subtitle = get_subtitle(uid)
        preview_text += f"\nQuality: <code>{quality}</code>"
        preview_text += f"\nSubtitle: <code>{subtitle}</code>"

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Download", callback_data=f"dl_{'_'.join(str(hash(u) % 10000) for u in urls[:3])}"),
                    InlineKeyboardButton("❌ Batal", callback_data="cancel_job"),
                ],
            ]
        )
        await message.reply(preview_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^cancel_job$"))
    async def cancel_job_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        if uid in active_jobs:
            active_jobs.discard(uid)
            await callback.message.edit_text("🛑 Job dibatalkan.")
        else:
            await callback.message.edit_text("Tidak ada job yang berjalan.")

    @app.on_callback_query(filters.regex("^dl_"))
    async def download_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        if uid in active_jobs:
            await callback.message.edit_text("⏳ Job sudah berjalan.")
            return
        await callback.message.edit_text("🚀 Memulai download...")
        text = callback.message.text or ""
        url_match = re.findall(r"<code>([^<]+)</code>", text)
        if not url_match:
            await callback.message.edit_text("❌ Gagal parse URL.")
            return
        urls = [f"https://hstream.moe/hentai/{u}" for u in url_match if not u.startswith("http")]
        if not urls:
            await callback.message.edit_text("❌ Tidak ada URL valid.")
            return
        active_jobs.add(uid)
        try:
            await _process_urls(client, callback.message, urls, settings, executor, active_jobs, user_dir, user_cookies_path, get_quality, get_subtitle)
        finally:
            active_jobs.discard(uid)


async def _handle_search(
    client: Client,
    message: Message,
    query: str,
    settings: Settings,
    executor: ThreadPoolExecutor,
) -> None:
    uid = message.from_user.id
    status = await message.reply(
        f"🔍 Mencari <code>{html_escape(query)}</code>...",
        parse_mode=enums.ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()

    def _search() -> list[dict[str, str]]:
        import requests as req
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        try:
            r = req.get(
                f"https://hstream.moe/search?q={query}",
                headers=headers,
                timeout=30,
            )
            if r.status_code != 200:
                return []
            page = r.text
            results = []
            pattern = re.compile(
                r'href=["\'](/hentai/[^"\']+)["\'][^>]*>.*?<img[^>]+src=["\']([^"\']+)["\'].*?</a>',
                re.I | re.S,
            )
            for m in pattern.finditer(page):
                url = m.group(1)
                thumb = m.group(2)
                slug = url.rstrip("/").split("/")[-1]
                title = slug.replace("-", " ").title()
                full_url = f"https://hstream.moe{url}" if not url.startswith("http") else url
                full_thumb = f"https://hstream.moe{thumb}" if not thumb.startswith("http") else thumb
                results.append({
                    "url": full_url,
                    "title": title,
                    "thumb": full_thumb,
                })
            return results[:10]
        except Exception:
            return []

    results = await loop.run_in_executor(executor, _search)

    if not results:
        await status.edit_text(
            "🔍 Tidak ditemukan hasil.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    text = f"🔍 <b>Hasil Pencarian</b> — {len(results)} result(s)\n\n"
    kb_buttons: list[list[InlineKeyboardButton]] = []
    for i, r in enumerate(results, 1):
        text += f"{i}. <b>{html_escape(r['title'])}</b>\n"
        text += f"   <code>{r['url']}</code>\n"
        if i <= 5:
            kb_buttons.append([
                InlineKeyboardButton(
                    f"{i}. {r['title'][:20]}",
                    callback_data=f"search_{i}",
                ),
            ])

    await status.edit_text(text, parse_mode=enums.ParseMode.HTML)


async def _handle_batch(
    client: Client,
    message: Message,
    series_url: str,
    settings: Settings,
    executor: ThreadPoolExecutor,
    active_jobs: set[int],
    user_dir_fn: object,
    user_cookies_path_fn: object,
    get_quality_fn: object,
    get_subtitle_fn: object,
) -> None:
    uid = message.from_user.id
    if uid in active_jobs:
        await message.reply("⏳ Job sudah berjalan.")
        return

    if not URL_RE.match(series_url):
        await message.reply(
            "❌ Link tidak valid.\n"
            "Format: <code>https://hstream.moe/hentai/title</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    status = await message.reply(
        f"📦 <b>Mencari episodes...</b>\n\n"
        f"<code>{html_escape(series_url)}</code>",
        parse_mode=enums.ParseMode.HTML,
    )

    loop = asyncio.get_running_loop()
    cookies = settings.cookies_dir / f"{uid}.txt"
    cookies_file = cookies if cookies.exists() else None

    episodes = await loop.run_in_executor(
        executor,
        lambda: scrape_episode_list(series_url, cookies_file=cookies_file),
    )

    if not episodes:
        await status.edit_text(
            "❌ Tidak ditemukan episode.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    quality = get_quality_fn(uid) if callable(get_quality_fn) else settings.default_quality
    subtitle = get_subtitle_fn(uid) if callable(get_subtitle_fn) else settings.default_subtitle

    text = (
        f"📦 <b>Batch Download</b>\n\n"
        f"Ditemukan <b>{len(episodes)}</b> episode\n"
        f"Quality: <code>{quality}</code>\n"
        f"Subtitle: <code>{subtitle}</code>\n\n"
        "Konfirmasi download?"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"✅ Download All ({len(episodes)} eps)",
                    callback_data=f"batch_{uid}_{len(episodes)}",
                ),
            ],
            [
                InlineKeyboardButton("❌ Batal", callback_data="cancel_job"),
            ],
        ]
    )
    await status.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)


async def _process_urls(
    client: Client,
    message: Message,
    urls: list[str],
    settings: Settings,
    executor: ThreadPoolExecutor,
    active_jobs: set[int],
    user_dir_fn: object,
    user_cookies_path_fn: object,
    get_quality_fn: object = None,
    get_subtitle_fn: object = None,
) -> None:
    uid = message.from_user.id
    dest = settings.download_root / str(uid)
    dest.mkdir(parents=True, exist_ok=True)

    cookies = settings.cookies_dir / f"{uid}.txt"
    cookies_file = cookies if cookies.exists() else None

    quality = get_quality_fn(uid) if callable(get_quality_fn) else settings.default_quality
    subtitle = get_subtitle_fn(uid) if callable(get_subtitle_fn) else settings.default_subtitle

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
        f"🚀 <b>Memulai Download</b>\n\n"
        f"📺 <b>{total_eps}</b> episode dari <b>{total_series}</b> series\n"
        f"🍪 Cookies: {'✅' if cookies_file else '❌'}\n"
        f"🎬 Quality: <code>{quality}</code>\n"
        f"💬 Subtitle: <code>{subtitle}</code>\n"
        f"📤 Upload via: <b>Kurigram MTProto</b>",
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
            f"📚 <b>Series [{s_idx}/{total_series}]</b>\n"
            f"{html_escape(title_label)}\n"
            f"Episode: <b>{len(series_urls)}</b>",
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
                await progress_edit(status, "🛑 Job dibatalkan.")
                return

            progress_cb = make_progress_cb(status, idx, total_eps, url, settings, loop)

            try:
                final_path: Path = await loop.run_in_executor(
                    executor,
                    lambda _u=url: process_url(
                        _u, dest,
                        cookies_file=cookies_file,
                        progress=progress_cb,
                        quality=quality,
                        subtitle_lang=subtitle,
                    ),
                )
            except Exception as e:
                logger.exception("Failed %s", url)
                text = (
                    f"❌ <b>[{idx}/{total_eps}]</b> Gagal download\n\n"
                    f"<code>{url}</code>\n\n"
                    f"Error: <code>{html_escape(str(e))}</code>\n\n"
                    "💡 Coba /cookies dulu, atau kirim link lain."
                )
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔄 Coba Lagi", callback_data="retry_download")]]
                )
                await progress_edit(status, text)
                continue

            ep_num = episode_number_from_url(url)
            final_path = rename_episode_file(final_path, anime_title, ep_num)
            size_mb = final_path.stat().st_size / (1024 * 1024)
            has_subs = final_path.suffix.lower() == ".mkv"
            ep_caption = build_episode_caption(anime_title, ep_num, final_path, has_subs)

            save_download_history(
                settings.history_dir,
                uid,
                url,
                final_path.name,
                final_path.stat().st_size,
                quality,
            )

            await progress_edit(
                status,
                f"✅ <b>[{idx}/{total_eps}]</b> Siap upload\n\n"
                f"<code>{final_path.name}</code>\n"
                f"📏 {human_size(final_path.stat().st_size)}",
            )

            if size_mb > settings.max_file_mb:
                text = (
                    f"📦 <b>File terlalu besar</b>\n\n"
                    f"📏 {size_mb:.1f} MB > {settings.max_file_mb:.0f} MB\n"
                    f"<code>{final_path.name}</code>"
                )
                await message.reply(text, parse_mode=enums.ParseMode.HTML)
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
                    text = (
                        f"⚠️ <b>Upload gagal</b>\n\n"
                        f"Error: <code>{html_escape(str(e))}</code>"
                    )
                    await message.reply(text, parse_mode=enums.ParseMode.HTML)

            if not settings.keep_files and uploaded_ok:
                try:
                    final_path.unlink(missing_ok=True)
                except Exception:
                    pass

            if settings.notify_dm and uid != message.chat.id:
                try:
                    await client.send_message(
                        chat_id=uid,
                        text=f"✅ <b>Upload selesai</b>\n\n<code>{final_path.name}</code>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass

    if settings.auto_delete_days > 0:
        removed = await loop.run_in_executor(
            executor,
            lambda: cleanup_old_files(settings.download_root, settings.auto_delete_days),
        )
        if removed > 0:
            logger.info("Auto-deleted %d old files", removed)

    await progress_edit(
        status,
        f"🎉 <b>Selesai!</b>\n\n"
        f"📺 {total_eps} episode dari {total_series} series\n"
        "Ketik /status atau /clear untuk kelola file.",
    )
