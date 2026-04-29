"""Pipeline orchestrator: fetch -> classify -> summarize -> save."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from its_briefing import config, db, fetch, llm, storage
from its_briefing.models import Briefing

logger = logging.getLogger(__name__)


def run() -> Optional[Briefing]:
    """Run the full briefing pipeline. Returns the saved Briefing or None on failure.

    Top-level try/except guarantees no exception propagates to the scheduler.
    """
    load_dotenv()
    run_id: Optional[int] = None
    try:
        # --- DB setup (now inside the try so failures don't break the scheduler) ---
        conn = db.get_connection()
        try:
            db.init_schema(conn)
            db.seed_settings_from_env(conn, config.Settings.from_env())
            settings = db.get_settings(conn)
            run_id = db.record_run_start(conn)
        finally:
            conn.close()

        # --- pipeline ---
        sources = config.load_sources(enabled_only=True)
        categories = config.load_categories()

        logger.info("Fetching %d sources...", len(sources))
        articles, failed_sources = fetch.fetch_all(sources)
        logger.info(
            "Fetched %d articles, %d sources failed", len(articles), len(failed_sources)
        )

        # Back-feed health status into the sources table.
        try:
            status_conn = db.get_connection()
            try:
                rows = db.list_sources(status_conn)
                name_to_id = {r["name"]: r["id"] for r in rows}
                failed_set = set(failed_sources)
                for s in sources:
                    sid = name_to_id.get(s.name)
                    if sid is None:
                        continue
                    if s.name in failed_set:
                        db.record_source_check_result(
                            status_conn, sid, status="failed", error="fetch failed"
                        )
                    else:
                        db.record_source_check_result(
                            status_conn, sid, status="ok", error=None
                        )
            finally:
                status_conn.close()
        except Exception:  # noqa: BLE001 -- never break the pipeline on status writes
            logger.exception("Failed to back-feed source statuses; continuing")

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

        storage.save_briefing(briefing)

        finish_conn = db.get_connection()
        try:
            db.record_run_finish(
                finish_conn,
                run_id,
                succeeded=True,
                article_count=briefing.article_count,
                error=None,
            )
        finally:
            finish_conn.close()

        logger.info(
            "Briefing for %s generated: %d articles, %d failed sources",
            target_date.isoformat(),
            briefing.article_count,
            len(failed_sources),
        )
        return briefing

    except Exception as exc:  # noqa: BLE001 -- top-level guard
        logger.exception("Briefing generation failed")
        if run_id is not None:
            try:
                finish_conn = db.get_connection()
                try:
                    db.record_run_finish(
                        finish_conn,
                        run_id,
                        succeeded=False,
                        article_count=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    finish_conn.close()
            except Exception:  # noqa: BLE001 -- never raise from the top-level guard
                logger.exception("Failed to record failed run; continuing")
        return None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run()
