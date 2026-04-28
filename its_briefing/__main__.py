"""Process entry point: starts Flask + APScheduler in one process.

Usage:
    python -m its_briefing
"""
from __future__ import annotations

import logging
import signal
import sys

from dotenv import load_dotenv

from its_briefing import db, scheduler
from its_briefing.app import create_app
from its_briefing.config import Settings


def main() -> None:
    load_dotenv()
    env_settings = Settings.from_env()

    logging.basicConfig(
        level=getattr(logging, env_settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # First-boot DB init + settings seed.
    conn = db.get_connection()
    try:
        db.init_schema(conn)
        db.seed_settings_from_env(conn, env_settings)
        settings = db.get_settings(conn)
    finally:
        conn.close()

    scheduler.start(settings)

    def _graceful_exit(signum, frame):  # noqa: ARG001
        logging.info("Shutting down…")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    app = create_app()
    # flask_host/flask_port are process-bound — read from env on every start.
    app.run(
        host=env_settings.flask_host,
        port=env_settings.flask_port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
