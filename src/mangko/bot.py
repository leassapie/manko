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

from mangko.config import Settings
from mangko.downloader import (
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
from mangko.search import SitemapSearch
from mangko.thumb import (
    create_user_thumb,
    download_poster_thumb,
    resolve_doc_thumb,
    user_thumb_path,
)
from mangko.uploader import (
    build_episode_caption,
    build_series_caption,
    media_destinations,
    make_progress_cb,
    make_upload_progress,
    progress_edit,
    send_document_no_reply,
    send_photo_no_reply,
)
from mangko.utils import (
    episode_number_from_url,
    html_escape,
    human_size,
    progress_bar,
    rename_episode_file,
    sys_stats_line,
)

logger = logging.getLogger("mangko")

URL_RE = re.compile(r"https?://(?:www\.)?hstream\.moe/hentai/[\w\-]+/?", re.I)

# Global search engine and result cache
_search_engine = SitemapSearch()
_search_results: dict[int, list] = {}  # uid → list of SearchResult


def create_app(settings: Settings) -> Client:
    return Client(
        settings.session_name,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        workdir=str(Path(".").resolve()),
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 Download", callback_data="menu_download"),
                InlineKeyboardButton("🔍 Search", callback_data="menu_search"),
            ],
            [
                InlineKeyboardButton("📦 Batch", callback_data="menu_batch"),
                InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data="menu_status"),
                InlineKeyboardButton("📜 History", callback_data="menu_history"),
            ],
        ]
    )


def back_kb(target: str = "menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Kembali", callback_data=target)]]
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

    def clear_flag(prefix: str, uid: int) -> None:
        flag = settings.cookies_dir / f"{prefix}_{uid}"
        flag.unlink(missing_ok=True)

    def set_flag(prefix: str, uid: int) -> None:
        (settings.cookies_dir / f"{prefix}_{uid}").touch()

    def has_flag(prefix: str, uid: int) -> bool:
        return (settings.cookies_dir / f"{prefix}_{uid}").exists()

    # ── /start ──────────────────────────────────────────
    @app.on_message(filters.command("start"))
    async def start_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        cookies_ok = user_cookies_path(uid).exists()
        quality = get_quality(uid)
        subtitle = get_subtitle(uid)

        status_line = (
            f"🍪 {'✅' if cookies_ok else '❌'} • "
            f"🎬 {quality} • "
            f"💬 {subtitle}"
        )
        text = (
            "👋 <b>Mangko</b>\n\n"
            "Download episode hstream.moe langsung ke Telegram.\n"
            "Quality terbaik, subtitle pilihan, remux MKV.\n\n"
            f"⚙️ {status_line}\n\n"
            "💡 Kirim link episode untuk langsung download,\n"
            "atau gunakan tombol di bawah."
        )
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=main_menu_kb())

    # ── Main Menu ───────────────────────────────────────
    @app.on_callback_query(filters.regex("^menu$"))
    async def menu_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        cookies_ok = user_cookies_path(uid).exists()
        quality = get_quality(uid)
        subtitle = get_subtitle(uid)

        status_line = (
            f"🍪 {'✅' if cookies_ok else '❌'} • "
            f"🎬 {quality} • "
            f"💬 {subtitle}"
        )
        text = (
            "👋 <b>Mangko</b>\n\n"
            "Download episode hstream.moe langsung ke Telegram.\n"
            "Quality terbaik, subtitle pilihan, remux MKV.\n\n"
            f"⚙️ {status_line}\n\n"
            "💡 Kirim link episode untuk langsung download,\n"
            "atau gunakan tombol di bawah."
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=main_menu_kb())

    # ── Download Menu ───────────────────────────────────
    @app.on_callback_query(filters.regex("^menu_download$"))
    async def download_menu_cb(client: Client, callback: CallbackQuery) -> None:
        text = (
            "📥 <b>Download</b>\n\n"
            "Kirim link episode hstream.moe.\n"
            "Contoh:\n"
            "<code>https://hstream.moe/hentai/title-1</code>\n\n"
            "💡 Bisa kirim beberapa link sekaligus\n"
            "(satu link per baris)."
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=back_kb())

    # ── Search ──────────────────────────────────────────
    @app.on_callback_query(filters.regex("^menu_search$"))
    async def search_menu_cb(client: Client, callback: CallbackQuery) -> None:
        set_flag("await_search", callback.from_user.id)
        text = (
            "🔍 <b>Search Anime</b>\n\n"
            "Kirim judul anime yang ingin dicari.\n"
            "Contoh: <code>Yuki</code>"
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=back_kb())

    # ── Batch ───────────────────────────────────────────
    @app.on_callback_query(filters.regex("^menu_batch$"))
    async def batch_menu_cb(client: Client, callback: CallbackQuery) -> None:
        set_flag("await_batch", callback.from_user.id)
        text = (
            "📦 <b>Batch Download</b>\n\n"
            "Kirim link series untuk download semua episode.\n"
            "Contoh:\n"
            "<code>https://hstream.moe/hentai/title</code>\n\n"
            "⚠️ Tanpa angka episode di akhir URL."
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=back_kb())

    # ── Settings ────────────────────────────────────────
    @app.on_callback_query(filters.regex("^menu_settings$"))
    async def settings_menu_cb(client: Client, callback: CallbackQuery) -> None:
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
            f"💬 Subtitle: <code>{subtitle}</code>"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🍪 Cookies", callback_data="set_cookies"),
                    InlineKeyboardButton("🖼 Thumb", callback_data="set_thumb"),
                ],
                [
                    InlineKeyboardButton("🎬 Quality", callback_data="set_quality"),
                    InlineKeyboardButton("💬 Subtitle", callback_data="set_subtitle"),
                ],
                [InlineKeyboardButton("◀️ Kembali", callback_data="menu")],
            ]
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    # ── Cookies ─────────────────────────────────────────
    @app.on_callback_query(filters.regex("^set_cookies$"))
    async def cookies_btn_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        has_cookies = user_cookies_path(uid).exists()
        status = "✅ Sudah ada" if has_cookies else "❌ Belum ada"
        set_flag("await", uid)
        text = (
            "🍪 <b>Setup Cookies</b>\n\n"
            f"Status: {status}\n\n"
            "Kirim file <code>cookies.txt</code> (Netscape format)\n"
            "sekarang."
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=back_kb("menu_settings"))

    # ── Thumbnail ───────────────────────────────────────
    @app.on_callback_query(filters.regex("^set_thumb$"))
    async def thumb_btn_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        path = user_thumb_path(uid)
        status = "✅ Sudah ada" if path.exists() else "❌ Belum ada"
        set_flag("await_thumb", uid)
        text = (
            "🖼 <b>Setup Thumbnail</b>\n\n"
            f"Status: {status}\n\n"
            "Kirim foto untuk dijadikan thumbnail upload."
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=back_kb("menu_settings"))

    # ── Quality ─────────────────────────────────────────
    @app.on_callback_query(filters.regex("^set_quality$"))
    async def quality_btn_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
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
                [InlineKeyboardButton("◀️ Kembali", callback_data="menu_settings")],
            ]
        )
        text = (
            f"🎬 <b>Quality</b>\n\n"
            f"Sekarang: <code>{current}</code>"
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^q_(best|2160p|1080p|720p|480p|360p)$"))
    async def quality_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        quality = callback.data.split("_", 1)[1]
        user_quality[uid] = quality
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Kembali", callback_data="menu_settings")]]
        )
        await callback.message.edit_text(
            f"✅ Quality diubah ke <code>{quality}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
        )

    # ── Subtitle ────────────────────────────────────────
    @app.on_callback_query(filters.regex("^set_subtitle$"))
    async def subtitle_btn_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
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
                [InlineKeyboardButton("◀️ Kembali", callback_data="menu_settings")],
            ]
        )
        text = (
            f"💬 <b>Subtitle</b>\n\n"
            f"Sekarang: <code>{current}</code>"
        )
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    @app.on_callback_query(filters.regex("^sub_(en|id|jp)$"))
    async def subtitle_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        subtitle = callback.data.split("_", 1)[1]
        user_subtitle[uid] = subtitle
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Kembali", callback_data="menu_settings")]]
        )
        await callback.message.edit_text(
            f"✅ Subtitle diubah ke <code>{subtitle}</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
        )

    # ── Status ──────────────────────────────────────────
    @app.on_callback_query(filters.regex("^menu_status$"))
    async def status_menu_cb(client: Client, callback: CallbackQuery) -> None:
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
            f"📊 <b>Status</b>\n\n"
            f"📂 Files: <b>{len(files)}</b>\n"
            f"💾 Size: <b>{human_size(total)}</b>\n"
            f"⚙️ Jobs: <b>{jobs} active</b>\n"
            f"🍪 Cookies: {'✅' if cookies_ok else '❌'}\n"
            f"🖼 Thumbnail: {'✅' if thumb_ok else '❌'}\n"
            f"🎬 Quality: <code>{quality}</code>\n"
            f"💬 Subtitle: <code>{subtitle}</code>"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="menu_status"),
                    InlineKeyboardButton("🧹 Clear", callback_data="status_clear"),
                ],
                [InlineKeyboardButton("◀️ Kembali", callback_data="menu")],
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
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Kembali", callback_data="menu_status")]]
        )
        await callback.message.edit_text(
            f"🧹 Berhasil hapus {removed} item(s).",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
        )

    # ── History ─────────────────────────────────────────
    @app.on_callback_query(filters.regex("^menu_history$"))
    async def history_menu_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        history = get_download_history(settings.history_dir, uid, limit=5)
        if not history:
            text = "📜 <b>History</b>\n\nBelum ada download."
            kb = back_kb()
        else:
            text = "📜 <b>History</b> (5 terakhir)\n\n"
            kb_buttons: list[list[InlineKeyboardButton]] = []
            for i, h in enumerate(history, 1):
                filename = h.get("filename", "unknown")
                size = human_size(h.get("size", 0))
                quality = h.get("quality", "?")
                text += f"{i}. <code>{filename}</code>\n   📏 {size} • 🎬 {quality}\n"
            kb_buttons.append([InlineKeyboardButton("◀️ Kembali", callback_data="menu")])
            kb = InlineKeyboardMarkup(kb_buttons)
        await callback.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    # ── Help ────────────────────────────────────────────
    @app.on_message(filters.command("help"))
    async def help_cmd(client: Client, message: Message) -> None:
        text = (
            "❓ <b>Bantuan</b>\n\n"
            "<b>Cara Pakai</b>\n"
            "1. /cookies lalu kirim cookies.txt\n"
            "2. (Optional) /thumb lalu kirim foto\n"
            "3. (Optional) /quality pilih resolusi\n"
            "4. (Optional) /subtitle pilih bahasa\n"
            "5. Kirim link episode hstream.moe\n\n"
            "<b>Commands</b>\n"
            "/quality — pilih resolusi\n"
            "/subtitle — pilih bahasa\n"
            "/search — cari anime\n"
            "/batch — download semua episode\n"
            "/history — riwayat download\n"
            "/stats — statistik\n\n"
            "💡 Atau gunakan tombol di menu utama."
        )
        kb = back_kb()
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    # ── Stats ───────────────────────────────────────────
    @app.on_message(filters.command("stats"))
    async def stats_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        stats = get_user_stats(settings.history_dir, uid)
        text = (
            f"📊 <b>Stats</b>\n\n"
            f"📥 Total download: <b>{stats['total_downloads']}</b>\n"
            f"💾 Total size: <b>{human_size(stats['total_size'])}</b>"
        )
        if stats["formats"]:
            text += "\n\n📊 <b>Quality Distribution</b>\n"
            for q, count in stats["formats"].items():
                text += f"• {q}: <b>{count}</b>\n"
        kb = back_kb()
        await message.reply(text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    # ── Cancel ──────────────────────────────────────────
    @app.on_message(filters.command("cancel"))
    async def cancel_cmd(client: Client, message: Message) -> None:
        uid = message.from_user.id
        if uid in active_jobs:
            active_jobs.discard(uid)
            await message.reply("🛑 Job dibatalkan.")
        else:
            await message.reply("Tidak ada job yang berjalan.")

    # ── Clear ───────────────────────────────────────────
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
        kb = back_kb()
        await message.reply(f"🧹 Berhasil hapus {removed} item(s).", reply_markup=kb)

    # ── Document Handler (cookies) ──────────────────────
    @app.on_message(filters.document)
    async def handle_document(client: Client, message: Message) -> None:
        uid = message.from_user.id
        if not has_flag("await", uid):
            return
        doc: Document = message.document
        name = (doc.file_name or "").lower()
        if not name.endswith((".txt", ".cookies")):
            kb = back_kb("menu_settings")
            await message.reply(
                "❌ File tidak valid.\nKirim file <code>cookies.txt</code>.",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb,
            )
            return
        dest = user_cookies_path(uid)
        await message.download(file_name=str(dest))
        clear_flag("await", uid)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Kembali", callback_data="menu_settings")]]
        )
        await message.reply(
            f"✅ Cookies tersimpan!\n📏 {human_size(dest.stat().st_size)}",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
        )

    # ── Photo Handler (thumbnail) ───────────────────────
    @app.on_message(filters.photo)
    async def handle_photo(client: Client, message: Message) -> None:
        uid = message.from_user.id
        if not has_flag("await_thumb", uid):
            return
        dl = await message.download()
        out = create_user_thumb(Path(dl), uid)
        Path(dl).unlink(missing_ok=True)
        clear_flag("await_thumb", uid)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Kembali", callback_data="menu_settings")]]
        )
        if out:
            await message.reply("✅ Thumbnail tersimpan!", reply_markup=kb)
        else:
            await message.reply("❌ Gagal simpan thumbnail.", reply_markup=kb)

    # ── Text Handler (URLs + search + batch) ────────────
    @app.on_message(filters.text & ~filters.command([
        "start", "help", "cancel", "clear", "stats",
    ]))
    async def handle_text(client: Client, message: Message) -> None:
        uid = message.from_user.id
        text = (message.text or "").strip()

        if has_flag("await_search", uid):
            clear_flag("await_search", uid)
            await _handle_search(client, message, text, settings, executor)
            return

        if has_flag("await_batch", uid):
            clear_flag("await_batch", uid)
            await _handle_batch(client, message, text, settings, executor, active_jobs, user_dir, user_cookies_path, get_quality, get_subtitle)
            return

        urls = URL_RE.findall(text)
        if not urls:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀️ Kembali", callback_data="menu")]]
            )
            await message.reply(
                "🔍 Link tidak ditemukan.\n\n"
                "Format: <code>https://hstream.moe/hentai/title-1</code>",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb,
            )
            return

        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]

        if uid in active_jobs:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🛑 Batalkan", callback_data="cancel_job")]]
            )
            await message.reply(
                "⏳ Job sudah berjalan.",
                reply_markup=kb,
            )
            return

        quality = get_quality(uid)
        subtitle = get_subtitle(uid)

        preview_urls = urls[:5]
        preview_text = "🔍 <b>Preview</b>\n\n"
        for i, u in enumerate(preview_urls, 1):
            slug = u.rstrip("/").split("/")[-1]
            preview_text += f"{i}. <code>{slug}</code>\n"
        if len(urls) > 5:
            preview_text += f"... dan {len(urls) - 5} lagi\n"
        preview_text += f"\nTotal: <b>{len(urls)}</b> episode"
        preview_text += f"\nQuality: <code>{quality}</code>"
        preview_text += f"\nSubtitle: <code>{subtitle}</code>"

        url_hash = "_".join(str(hash(u) % 10000) for u in urls[:3])
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(f"✅ Download ({len(urls)} eps)", callback_data=f"dl_{url_hash}"),
                ],
                [
                    InlineKeyboardButton("❌ Batal", callback_data="cancel_job"),
                ],
            ]
        )
        await message.reply(preview_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)

    # ── Download Callback ───────────────────────────────
    @app.on_callback_query(filters.regex("^dl_"))
    async def download_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        if uid in active_jobs:
            await callback.message.edit_text("⏳ Job sudah berjalan.")
            return
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
            await callback.message.edit_text("🚀 Memulai download...")
            await _process_urls(client, callback.message, urls, settings, executor, active_jobs, user_dir, user_cookies_path, get_quality, get_subtitle)
        finally:
            active_jobs.discard(uid)

    # ── Cancel Job Callback ─────────────────────────────
    @app.on_callback_query(filters.regex("^cancel_job$"))
    async def cancel_job_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        if uid in active_jobs:
            active_jobs.discard(uid)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Kembali", callback_data="menu")]]
        )
        await callback.message.edit_text("🛑 Job dibatalkan.", reply_markup=kb)

    # ── Search Result Download Callback ────────────────
    @app.on_callback_query(filters.regex(r"^search_dl_(\d+)$"))
    async def search_download_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        idx = int(callback.data.split("_")[-1])
        results = _search_results.get(uid, [])
        if idx >= len(results):
            await callback.answer("Result expired, search again.", show_alert=True)
            return
        r = results[idx]
        # Scrape full series info for episode list
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            executor, lambda: _search_engine.get_series_info(r.url)
        )
        ep_count = info.episodes or "?"
        tags_str = ", ".join(info.tags[:6]) if info.tags else "—"
        text = (
            f"📖 <b>{html_escape(info.title)}</b>\n"
            f"🏷️ Tags: {html_escape(tags_str)}\n"
            f"📺 Episodes: <code>{ep_count}</code>\n"
            f"📅 Year: <code>{info.year or '—'}</code>\n"
            f"🌐 Studio: {html_escape(info.studio or '—')}\n\n"
            f"💡 Kirim link episode untuk download,\n"
            f"atau gunakan /batch untuk semua episode."
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📦 Batch Download",
                        callback_data=f"search_batch_{idx}",
                    ),
                ],
                [
                    InlineKeyboardButton("◀️ Kembali", callback_data="menu"),
                ],
            ]
        )
        await callback.message.edit_text(
            text, parse_mode=enums.ParseMode.HTML, reply_markup=kb
        )
        await callback.answer()

    # ── Search Result Batch Callback ───────────────────
    @app.on_callback_query(filters.regex(r"^search_batch_(\d+)$"))
    async def search_batch_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        idx = int(callback.data.split("_")[-1])
        results = _search_results.get(uid, [])
        if idx >= len(results):
            await callback.answer("Result expired, search again.", show_alert=True)
            return
        r = results[idx]
        if uid in active_jobs:
            await callback.answer("Job sudah berjalan.", show_alert=True)
            return

        # Scrape episode list from series URL
        loop = asyncio.get_running_loop()
        cookies = settings.cookies_dir / f"{uid}.txt"
        cookies_file = cookies if cookies.exists() else None

        await callback.message.edit_text(
            f"📦 <b>Memuat episodes...</b>\n\n"
            f"<b>{html_escape(r.title)}</b>"
        )

        episodes = await loop.run_in_executor(
            executor,
            lambda: scrape_episode_list(r.url, cookies_file=cookies_file),
        )

        if not episodes:
            await callback.message.edit_text(
                "❌ Tidak ditemukan episode.",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        quality = get_quality(uid)
        subtitle = get_subtitle(uid)
        text = (
            f"📦 <b>Batch Download</b>\n\n"
            f"📖 <b>{html_escape(r.title)}</b>\n"
            f"📺 Ditemukan <b>{len(episodes)}</b> episode\n"
            f"🎬 Quality: <code>{quality}</code>\n"
            f"💬 Subtitle: <code>{subtitle}</code>\n\n"
            f"Konfirmasi download?"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"✅ Download All ({len(episodes)} eps)",
                        callback_data=f"search_confirm_batch_{idx}",
                    ),
                ],
                [
                    InlineKeyboardButton("❌ Batal", callback_data="cancel_job"),
                ],
            ]
        )
        await callback.message.edit_text(
            text, parse_mode=enums.ParseMode.HTML, reply_markup=kb
        )
        await callback.answer()

    # ── Search Confirm Batch Callback ──────────────────
    @app.on_callback_query(filters.regex(r"^search_confirm_batch_(\d+)$"))
    async def search_confirm_batch_cb(client: Client, callback: CallbackQuery) -> None:
        uid = callback.from_user.id
        idx = int(callback.data.split("_")[-1])
        results = _search_results.get(uid, [])
        if idx >= len(results):
            await callback.answer("Result expired, search again.", show_alert=True)
            return
        r = results[idx]
        if uid in active_jobs:
            await callback.answer("Job sudah berjalan.", show_alert=True)
            return

        # Scrape episode list
        loop = asyncio.get_running_loop()
        cookies = settings.cookies_dir / f"{uid}.txt"
        cookies_file = cookies if cookies.exists() else None

        episodes = await loop.run_in_executor(
            executor,
            lambda: scrape_episode_list(r.url, cookies_file=cookies_file),
        )

        if not episodes:
            await callback.message.edit_text(
                "❌ Tidak ditemukan episode.",
                parse_mode=enums.ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # Build episode URLs
        episode_urls = [ep["url"] for ep in episodes]
        active_jobs.add(uid)
        try:
            await callback.message.edit_text(
                f"🚀 <b>Memulai batch download...</b>\n\n"
                f"📖 {html_escape(r.title)}\n"
                f"📺 {len(episode_urls)} episodes"
            )
            await _process_urls(
                client, callback.message, episode_urls,
                settings, executor, active_jobs,
                user_dir, user_cookies_path, get_quality, get_subtitle,
            )
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
        reply_markup=back_kb(),
    )

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        executor, lambda: _search_engine.search(query, limit=10)
    )

    if not results:
        await status.edit_text(
            "🔍 Tidak ditemukan hasil.",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    _search_results[uid] = results
    await status.edit_text(
        f"🔍 Ditemukan <b>{len(results)}</b> hasil untuk "
        f"<code>{html_escape(query)}</code>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=back_kb(),
    )

    for i, r in enumerate(results[:10]):
        tags_str = ", ".join(r.tags[:5]) if r.tags else "—"
        ep_str = f"{r.episodes} eps" if r.episodes else "—"
        year_str = r.year or "—"
        caption = (
            f"📖 <b>{html_escape(r.title)}</b>\n"
            f"🏷️ Tags: {html_escape(tags_str)}\n"
            f"📺 Episodes: <code>{ep_str}</code>\n"
            f"📅 Year: <code>{year_str}</code>"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📥 Download",
                        callback_data=f"search_dl_{i}",
                    ),
                    InlineKeyboardButton(
                        "🔗 Open",
                        url=r.url,
                    ),
                ],
            ]
        )
        if r.poster_url:
            try:
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=r.poster_url,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=kb,
                )
            except Exception:
                await message.reply(
                    caption,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=kb,
                )
        else:
            await message.reply(
                caption,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=kb,
            )


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
        kb = back_kb()
        await message.reply("⏳ Job sudah berjalan.", reply_markup=kb)
        return

    if not URL_RE.match(series_url):
        kb = back_kb()
        await message.reply(
            "❌ Link tidak valid.\nFormat: <code>https://hstream.moe/hentai/title</code>",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
        )
        return

    kb = back_kb()
    status = await message.reply(
        f"📦 <b>Mencari episodes...</b>\n\n<code>{html_escape(series_url)}</code>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=kb,
    )

    loop = asyncio.get_running_loop()
    cookies = settings.cookies_dir / f"{uid}.txt"
    cookies_file = cookies if cookies.exists() else None

    episodes = await loop.run_in_executor(
        executor,
        lambda: scrape_episode_list(series_url, cookies_file=cookies_file),
    )

    if not episodes:
        kb = back_kb()
        await status.edit_text(
            "❌ Tidak ditemukan episode.",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=kb,
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
            [InlineKeyboardButton(f"✅ Download All ({len(episodes)} eps)", callback_data="confirm_batch")],
            [InlineKeyboardButton("❌ Batal", callback_data="cancel_job")],
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
        f"💬 Subtitle: <code>{subtitle}</code>",
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
                    f"Error: <code>{html_escape(str(e))}</code>"
                )
                kb = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔄 Retry", callback_data="retry_download")],
                        [InlineKeyboardButton("⏭ Skip", callback_data="skip_download")],
                    ]
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
                    kb = InlineKeyboardMarkup(
                        [[InlineKeyboardButton("📊 Status", callback_data="menu_status")]]
                    )
                    await client.send_message(
                        chat_id=uid,
                        text=f"✅ <b>Upload selesai</b>\n\n<code>{final_path.name}</code>",
                        parse_mode=enums.ParseMode.HTML,
                        reply_markup=kb,
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

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Status", callback_data="menu_status"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu"),
            ],
        ]
    )
    await progress_edit(
        status,
        f"🎉 <b>Selesai!</b>\n\n"
        f"📺 {total_eps} episode dari {total_series} series",
    )
