"""Basic tests for shared utilities."""

from __future__ import annotations

import json
import time
from pathlib import Path

from hstream_tg.downloader import (
    QUALITY_MAP,
    SUBTITLE_MAP,
    cleanup_old_files,
    get_download_history,
    get_user_stats,
    save_download_history,
)
from hstream_tg.utils import (
    episode_number_from_url,
    human_size,
    html_escape,
    progress_bar,
    rename_episode_file,
    sanitize_filename,
)


def test_human_size_bytes() -> None:
    assert human_size(0) == "0.0 B"
    assert human_size(512) == "512.0 B"


def test_human_size_kb() -> None:
    assert human_size(1024) == "1.0 KB"


def test_human_size_mb() -> None:
    assert human_size(1024 * 1024) == "1.0 MB"


def test_human_size_gb() -> None:
    assert human_size(1024**3) == "1.0 GB"


def test_progress_bar_full() -> None:
    assert progress_bar(100.0, width=5) == "●●●●●"


def test_progress_bar_empty() -> None:
    assert progress_bar(0.0, width=5) == "○○○○○"


def test_progress_bar_partial() -> None:
    result = progress_bar(50.0, width=10)
    assert result.count("●") == 5
    assert result.count("○") == 5


def test_html_escape() -> None:
    assert html_escape("<b>&\"'") == "&lt;b&gt;&amp;&quot;&#x27;"


def test_sanitize_filename() -> None:
    assert sanitize_filename('test/file:name') == "test file name"
    assert sanitize_filename("hello") == "hello"
    assert sanitize_filename("") == "file"


def test_episode_number_from_url() -> None:
    assert episode_number_from_url("https://hstream.moe/hentai/foo-1") == "1"
    assert episode_number_from_url("https://hstream.moe/hentai/foo-12") == "12"
    assert episode_number_from_url("https://hstream.moe/hentai/foo") == "?"


def test_sanitize_long_filename() -> None:
    long_name = "a" * 300
    result = sanitize_filename(long_name)
    assert len(result) <= 180


def test_quality_map() -> None:
    assert "best" in QUALITY_MAP
    assert "1080p" in QUALITY_MAP
    assert "720p" in QUALITY_MAP
    assert "480p" in QUALITY_MAP


def test_subtitle_map() -> None:
    assert SUBTITLE_MAP["en"] == "eng"
    assert SUBTITLE_MAP["id"] == "ind"
    assert SUBTITLE_MAP["jp"] == "jpn"


def test_save_and_get_history(tmp_path: Path) -> None:
    user_id = 12345
    save_download_history(
        tmp_path,
        user_id,
        "https://hstream.moe/hentai/test-1",
        "test.mkv",
        1024 * 1024,
        "1080p",
    )
    history = get_download_history(tmp_path, user_id)
    assert len(history) == 1
    assert history[0]["filename"] == "test.mkv"
    assert history[0]["quality"] == "1080p"


def test_get_user_stats(tmp_path: Path) -> None:
    user_id = 12345
    save_download_history(tmp_path, user_id, "url1", "f1.mkv", 1000, "1080p")
    save_download_history(tmp_path, user_id, "url2", "f2.mkv", 2000, "720p")
    stats = get_user_stats(tmp_path, user_id)
    assert stats["total_downloads"] == 2
    assert stats["total_size"] == 3000
    assert stats["formats"]["1080p"] == 1
    assert stats["formats"]["720p"] == 1


def test_cleanup_old_files(tmp_path: Path) -> None:
    old_file = tmp_path / "old.txt"
    old_file.write_text("old")
    old_file.touch()
    import os
    os.utime(old_file, (time.time() - 86400 * 10, time.time() - 86400 * 10))
    new_file = tmp_path / "new.txt"
    new_file.write_text("new")
    removed = cleanup_old_files(tmp_path, 5)
    assert removed >= 1
    assert not old_file.exists()
    assert new_file.exists()


def test_rename_episode_file(tmp_path: Path) -> None:
    src = tmp_path / "Some.File.720p.mkv"
    src.write_text("video")
    result = rename_episode_file(src, "Test Title", "1")
    assert result.name == "Test Title - 1.mkv"
    assert result.exists()
