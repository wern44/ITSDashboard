"""Flask web app for ITS-Briefing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template

from its_briefing import generate, scheduler, storage
from its_briefing.config import Settings, load_categories, load_sources

logger = logging.getLogger(__name__)


def create_app(settings: Optional[Settings] = None) -> Flask:
    """Application factory."""
    settings = settings or Settings.from_env()
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    categories = load_categories()
    source_count = len(load_sources())
    category_colors = {c.name: c.color for c in categories}

    @app.route("/")
    def index() -> str:
        briefing = storage.latest_briefing()
        return render_template(
            "briefing.html",
            briefing=briefing,
            category_colors=category_colors,
            source_count=source_count,
        )

    @app.route("/health")
    def health():
        latest = storage.latest_briefing()
        return jsonify(
            {
                "status": "ok",
                "last_briefing_date": latest.date.isoformat() if latest else None,
                "last_generated_at": latest.generated_at.isoformat() if latest else None,
                "next_scheduled_run": (
                    scheduler.next_run_time().isoformat() if scheduler.next_run_time() else None
                ),
            }
        )

    @app.route("/generate", methods=["POST"])
    def trigger_generate():
        briefing = generate.run()
        if briefing is None:
            return jsonify({"status": "error"}), 500
        return jsonify({"status": "ok", "date": briefing.date.isoformat()})

    return app
