"""Process entry point: starts Flask + APScheduler in one process.

Usage:
    python -m its_briefing
"""
from __future__ import annotations

import logging
import signal
import sys

from dotenv import load_dotenv

from its_briefing import scheduler
from its_briefing.app import create_app
from its_briefing.config import Settings


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    scheduler.start(settings)

    def _graceful_exit(signum, frame):  # noqa: ARG001
        logging.info("Shutting down…")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    app = create_app(settings)
    app.run(host=settings.flask_host, port=settings.flask_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
