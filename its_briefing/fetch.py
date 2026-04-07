"""Fetch RSS feeds and filter to the last 24 hours."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import httpx

from its_briefing.config import Source
from its_briefing.models import Article

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 10
MAX_WORKERS = 10
WINDOW_HOURS = 24


def _entry_published(entry: dict) -> Optional[datetime]:
    """Extract a UTC datetime from a feedparser entry, or None."""
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return datetime(*struct[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def parse_feed_bytes(raw: bytes, source: Source, now: datetime) -> list[Article]:
    """Parse raw feed bytes into Article objects, filtered to the last WINDOW_HOURS."""
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        return []

    cutoff = now - timedelta(hours=WINDOW_HOURS)
    articles: list[Article] = []
    for entry in parsed.entries:
        published = _entry_published(entry)
        if published is None or published < cutoff:
            continue
        link = entry.get("link") or ""
        title = entry.get("title") or ""
        summary = entry.get("summary") or entry.get("description") or ""
        if not link or not title:
            continue
        articles.append(
            Article(
                id=Article.make_id(link),
                source=source.name,
                source_lang=source.lang,
                title=title,
                link=link,
                published=published,
                summary=summary,
                category=None,
            )
        )
    return articles
