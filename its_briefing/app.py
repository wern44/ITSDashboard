"""Flask web app for ITS-Briefing."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from its_briefing import db, generate, scheduler, storage
from its_briefing.config import load_categories, load_sources

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Application factory."""
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

    @app.route("/settings", methods=["GET"])
    def settings_get():
        conn = db.get_connection()
        try:
            db.init_schema(conn)
            settings = db.get_settings(conn)
        finally:
            conn.close()
        saved = request.args.get("saved") == "1"
        return render_template("settings.html", settings=settings, saved=saved, error=None)

    @app.route("/settings", methods=["POST"])
    def settings_post():
        from apscheduler.triggers.cron import CronTrigger

        form = request.form
        provider = form.get("llm_provider", "")
        base_url = form.get("llm_base_url", "").strip()
        model = form.get("llm_model", "").strip()
        tz = form.get("timezone", "").strip()

        # ---- validation ----
        errors: list[str] = []
        if provider not in ("ollama", "lmstudio"):
            errors.append("provider must be 'ollama' or 'lmstudio'")
        if not base_url:
            errors.append("base_url is required")
        if not model:
            errors.append("model is required")
        try:
            hour = int(form.get("schedule_hour", ""))
            if not 0 <= hour <= 23:
                raise ValueError("hour out of range")
        except ValueError:
            errors.append("schedule_hour must be 0-23")
            hour = None
        try:
            minute = int(form.get("schedule_minute", ""))
            if not 0 <= minute <= 59:
                raise ValueError("minute out of range")
        except ValueError:
            errors.append("schedule_minute must be 0-59")
            minute = None

        # Validate timezone via APScheduler's CronTrigger (raises on bad tz).
        if not errors and tz:
            try:
                CronTrigger(hour=hour, minute=minute, timezone=tz)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"invalid timezone: {exc}")

        if errors:
            conn = db.get_connection()
            try:
                db.init_schema(conn)
                current = db.get_settings(conn)
            finally:
                conn.close()
            return (
                render_template(
                    "settings.html",
                    settings=current,
                    saved=False,
                    error="; ".join(errors),
                ),
                400,
            )

        # ---- save ----
        conn = db.get_connection()
        try:
            db.init_schema(conn)
            current = db.get_settings(conn)
            db.update_settings(
                conn,
                {
                    "llm_provider": provider,
                    "llm_base_url": base_url,
                    "llm_model": model,
                    "schedule_hour": hour,
                    "schedule_minute": minute,
                    "timezone": tz,
                },
            )
        finally:
            conn.close()

        # Reschedule if the cron-relevant fields changed.
        if (current.schedule_hour, current.schedule_minute, current.timezone) != (
            hour,
            minute,
            tz,
        ):
            try:
                scheduler.reschedule(hour, minute, tz)
            except RuntimeError:
                logger.info("Scheduler not running (likely under test); skipping reschedule")

        return redirect(url_for("settings_get") + "?saved=1")

    return app
