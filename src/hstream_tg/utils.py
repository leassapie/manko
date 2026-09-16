"""Shared utility helpers."""

import html as html_lib
import os
import re
import shutil
from pathlib import Path


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def progress_bar(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = round(width * pct / 100.0)
    return "●" * filled + "○" * (width - filled)


def html_escape(s: str) -> str:
    return html_lib.escape(s)


def sys_stats_line(download_root: Path) -> str:
    cpu = ram = disk = "?"
    try:
        load1, _, _ = os.getloadavg()
        cpu = f"{load1:.1f} load"
    except Exception:
        pass
    try:
        mem = Path("/proc/meminfo").read_text()
        vals: dict[str, int] = {}
        for line in mem.splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                k, v, *_ = line.split()
                vals[k.rstrip(":")] = int(v)
        if "MemTotal" in vals and "MemAvailable" in vals:
            used = vals["MemTotal"] - vals["MemAvailable"]
            ram = f"{100.0 * used / vals['MemTotal']:.1f}%"
    except Exception:
        pass
    try:
        usage = shutil.disk_usage(str(download_root))
        free_gb = usage.free / (1024**3)
        disk = f"{free_gb:.2f}GB free"
    except Exception:
        pass
    return f"CPU: {cpu} | RAM: {ram} | DISK: {disk}"


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:180] or "file"


def episode_number_from_url(url: str) -> str:
    token = url.rstrip("/").split("/")[-1]
    ep_match = re.search(r"-(\d+)$", token)
    return ep_match.group(1) if ep_match else "?"


def rename_episode_file(final_path: Path, anime_title: str, ep_num: str) -> Path:
    title = sanitize_filename(anime_title or final_path.stem)
    ext = final_path.suffix or ".mkv"
    new_name = f"{title} - {ep_num}{ext}"
    target = final_path.with_name(new_name)
    if target.resolve() == final_path.resolve():
        return final_path
    if target.exists():
        target.unlink()
    final_path.rename(target)
    return target
