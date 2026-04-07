"""Pipeline orchestrator: fetch -> classify -> summarize -> save."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from its_briefing import config, fetch, llm, storage
from its_briefing.models import Briefing

logger = logging.getLogger(__name__)


def run(cache_dir: Optional[Path] = None) -> Optional[Briefing]:
    """Run the full briefing pipeline. Returns the saved Briefing or None on failure."""
    try:
        load_dotenv()
        settings = config.Settings.from_env()
        sources = config.load_sources()
        categories = config.load_categories()

        logger.info("Fetching %d sources...", len(sources))
        articles, failed_sources = fetch.fetch_all(sources)
        logger.info(
            "Fetched %d articles, %d sources failed", len(articles), len(failed_sources)
        )

        for article in articles:
            article.category = llm.classify_article(article, categories, settings)

        now = datetime.now(timezone.utc)
        target_date = now.date()
        summary = llm.build_summary(articles, settings, target_date=target_date)

        briefing = Briefing(
            date=target_date,
            generated_at=now,
            summary=summary,
            articles=articles,
            failed_sources=failed_sources,
            article_count=len(articles),
        )

        if cache_dir is None:
            storage.save_briefing(briefing)
        else:
            storage.save_briefing(briefing, cache_dir=cache_dir)

        logger.info(
            "Briefing for %s generated: %d articles, %d failed sources",
            target_date.isoformat(),
            briefing.article_count,
            len(failed_sources),
        )
        return briefing

    except Exception:  # noqa: BLE001 -- top-level guard so the scheduler keeps running
        logger.exception("Briefing generation failed")
        return None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run()
