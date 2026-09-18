"""Sitemap-based fuzzy search for hstream.moe.

Uses async httpx, rapidfuzz for fast matching, tenacity for retry.
"""

import logging
import re
import time
from dataclasses import dataclass, field

import httpx
from rapidfuzz import fuzz
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger("mangko")

SITEMAP_URL = "https://hstream.moe/sitemap.xml"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
CACHE_TTL = 86400  # 24 hours


@dataclass
class SearchResult:
    url: str
    title: str
    slug: str
    poster_url: str = ""
    tags: list[str] = field(default_factory=list)
    episodes: int | None = None
    year: str = ""
    studio: str = ""
    score: float = 0.0


class SitemapSearch:
    """Fuzzy search over hstream.moe series using sitemap data."""

    def __init__(self) -> None:
        self._index: dict[str, str] = {}  # slug → series_url
        self._loaded_at: float = 0.0
        self._series_cache: dict[str, SearchResult] = {}

    def _ensure_loaded(self) -> None:
        if self._index and (time.time() - self._loaded_at) < CACHE_TTL:
            return
        self._load_sitemap()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _load_sitemap(self) -> None:
        try:
            with httpx.Client(timeout=30, headers=HEADERS) as client:
                r = client.get(SITEMAP_URL)
                if r.status_code != 200:
                    logger.warning("Sitemap HTTP %d", r.status_code)
                    return
            urls = re.findall(
                r"<loc>(https://hstream\.moe/hentai/[^<]+)</loc>", r.text
            )
            index: dict[str, str] = {}
            for url in urls:
                slug = url.rstrip("/").split("/")[-1]
                series_slug = re.sub(r"-\d+$", "", slug)
                series_url = f"https://hstream.moe/hentai/{series_slug}"
                if series_slug not in index:
                    index[series_slug] = series_url
            self._index = index
            self._loaded_at = time.time()
            logger.info("Sitemap loaded: %d series", len(index))
        except Exception as e:
            logger.warning("Sitemap load failed: %s", e)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Fuzzy search by query string. Returns top N results."""
        self._ensure_loaded()
        if not self._index:
            return []

        query_lower = query.lower().strip()
        scored: list[tuple[float, str, str]] = []

        for slug, url in self._index.items():
            slug_display = slug.replace("-", " ")
            score = self._score(query_lower, slug, slug_display)
            if score > 15:
                scored.append((score, slug, url))

        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[SearchResult] = []
        seen: set[str] = set()
        for score, slug, url in scored:
            if slug in seen:
                continue
            seen.add(slug)
            title = slug.replace("-", " ").title()
            results.append(SearchResult(
                url=url,
                title=title,
                slug=slug,
                score=score,
            ))
            if len(results) >= limit:
                break
        return results

    def _score(self, query: str, slug: str, display: str) -> float:
        """Multi-strategy scoring using rapidfuzz."""
        # Exact match
        if query == slug or query == display:
            return 100.0
        # Starts with
        if slug.startswith(query) or display.startswith(query):
            return 90.0
        # Contains
        if query in slug or query in display:
            return 80.0
        # Word match
        query_words = query.split()
        slug_words = display.split()
        word_hits = sum(1 for w in query_words if any(w in sw for sw in slug_words))
        if word_hits == len(query_words) and query_words:
            return 75.0
        # rapidfuzz weighted ratio
        return max(
            fuzz.weighted_ratio(query, slug),
            fuzz.weighted_ratio(query, display),
        )

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    def get_series_info(self, series_url: str) -> SearchResult:
        """Scrape individual series page for full metadata."""
        if series_url in self._series_cache:
            return self._series_cache[series_url]
        slug = series_url.rstrip("/").split("/")[-1]
        result = SearchResult(
            url=series_url,
            title=slug.replace("-", " ").title(),
            slug=slug,
        )
        try:
            import html as html_lib

            with httpx.Client(timeout=30, headers=HEADERS) as client:
                r = client.get(series_url)
                if r.status_code != 200:
                    return result
            page = r.text

            # Title
            m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.I | re.S)
            if m:
                result.title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if not result.title:
                m = re.search(r'property="og:title"\s+content="([^"]+)"', page)
                if m:
                    result.title = re.sub(
                        r"\s*-\s*Watch All.*$", "",
                        html_lib.unescape(m.group(1)),
                    ).strip()

            # Poster
            covers = re.findall(
                r'((?:https://hstream\.moe)?/images/hentai/[^"\']+/cover[^"\']+\.webp)',
                page, re.I,
            )
            if covers:
                u = covers[0]
                result.poster_url = u if u.startswith("http") else f"https://hstream.moe{u}"
            if not result.poster_url:
                m = re.search(r'property="og:image"\s+content="([^"]+)"', page)
                if m:
                    result.poster_url = m.group(1)

            # Tags
            tags = re.findall(
                r'tags(?:%5B0%5D|=)[^"\']*["\'][^>]*>\s*([^<\n]+)',
                page, re.I,
            )
            cleaned: list[str] = []
            for t in tags:
                t = re.sub(r"\s+", " ", t).strip()
                if t and t not in cleaned and len(t) < 40:
                    cleaned.append(t)
            result.tags = cleaned

            # Episodes
            m = re.search(r"Episodes\s*\((\d+)\)", page, re.I)
            if m:
                result.episodes = int(m.group(1))

            # Year
            dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", page)
            if dates:
                result.year = sorted(dates)[0][:4]

            # Studio
            m = re.search(
                r'/search\?[^"]*studios[^"]*"[^>]*>([^<]+)', page, re.I
            )
            if m:
                result.studio = re.sub(r"\s+", " ", m.group(1)).strip()

            self._series_cache[series_url] = result
        except Exception as e:
            logger.warning("Series scrape failed for %s: %s", series_url, e)
        return result
