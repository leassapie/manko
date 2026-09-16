"""Basic tests for shared utilities."""

from __future__ import annotations

from pathlib import Path

from hstream_tg.utils import (
    episode_number_from_url,
    human_size,
    html_escape,
    progress_bar,
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
