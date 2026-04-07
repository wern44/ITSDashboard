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


def _fetch_one(client: httpx.Client, source: Source, now: datetime) -> tuple[list[Article], Optional[str]]:
    """Fetch a single feed. Returns (articles, failed_source_name_or_None)."""
    try:
        response = client.get(source.url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Fetch failed for %s: %s", source.name, exc)
        return [], source.name

    try:
        articles = parse_feed_bytes(response.content, source, now=now)
    except Exception as exc:  # noqa: BLE001 — feedparser can raise odd things
        logger.warning("Parse failed for %s: %s", source.name, exc)
        return [], source.name

    return articles, None


def fetch_all(sources: list[Source]) -> tuple[list[Article], list[str]]:
    """Concurrently fetch all sources. Returns (articles, failed_source_names)."""
    now = datetime.now(timezone.utc)
    articles: list[Article] = []
    failed: list[str] = []

    with httpx.Client(headers={"User-Agent": "ITS-Briefing/0.1"}) as client:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_source = {
                executor.submit(_fetch_one, client, source, now): source for source in sources
            }
            for future in as_completed(future_to_source):
                got_articles, failure = future.result()
                articles.extend(got_articles)
                if failure:
                    failed.append(failure)

    articles.sort(key=lambda a: a.published, reverse=True)
    return articles, failed
