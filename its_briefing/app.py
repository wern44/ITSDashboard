"""Flask web app for ITS-Briefing."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from its_briefing import db, generate, scheduler, sources, storage
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

    @app.route("/api/test-connection", methods=["POST"])
    def test_connection():
        import time
        from its_briefing.llm import LLMClientError, LMStudioClient, OllamaClient

        body = request.get_json(silent=True) or {}
        provider = body.get("provider", "")
        base_url = (body.get("base_url") or "").strip()
        model = (body.get("model") or "").strip()

        if provider not in ("ollama", "lmstudio"):
            return jsonify({
                "ok": False,
                "models": [],
                "error": "provider must be 'ollama' or 'lmstudio'",
                "latency_ms": 0,
            })
        if not base_url:
            return jsonify({
                "ok": False,
                "models": [],
                "error": "base_url is required",
                "latency_ms": 0,
            })

        client = (
            OllamaClient(base_url, model)
            if provider == "ollama"
            else LMStudioClient(base_url, model)
        )
        start = time.perf_counter()
        try:
            models = client.list_models()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return jsonify({
                "ok": True,
                "models": models,
                "error": None,
                "latency_ms": latency_ms,
            })
        except LLMClientError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return jsonify({
                "ok": False,
                "models": [],
                "error": str(exc),
                "latency_ms": latency_ms,
            })

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

    @app.route("/sources", methods=["GET"])
    def sources_page():
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            rows = _db.list_sources(conn)
        finally:
            conn.close()
        return render_template("sources.html", sources=rows)

    def _validate_source_payload(payload: dict, *, partial: bool = False) -> tuple[dict, list[str]]:
        from urllib.parse import urlparse
        errors: list[str] = []
        out: dict = {}
        if "name" in payload or not partial:
            name = (payload.get("name") or "").strip()
            if not name:
                errors.append("name is required")
            else:
                out["name"] = name
        if "url" in payload or not partial:
            url = (payload.get("url") or "").strip()
            parsed = urlparse(url)
            if not url or not parsed.scheme or not parsed.netloc:
                errors.append("url must be an absolute http(s) URL")
            else:
                out["url"] = url
        if "lang" in payload or not partial:
            lang = (payload.get("lang") or "").strip().upper()
            if lang not in ("EN", "DE"):
                errors.append("lang must be 'EN' or 'DE'")
            else:
                out["lang"] = lang
        if "enabled" in payload:
            out["enabled"] = bool(payload["enabled"])
        return out, errors

    @app.route("/api/sources", methods=["POST"])
    def api_sources_create():
        from its_briefing import db as _db
        payload = request.get_json(silent=True) or {}
        data, errors = _validate_source_payload(payload, partial=False)
        if errors:
            return jsonify({"errors": errors}), 400
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            try:
                sid = _db.create_source(
                    conn,
                    name=data["name"],
                    url=data["url"],
                    lang=data["lang"],
                    enabled=data.get("enabled", True),
                )
            except sqlite3.IntegrityError:
                return jsonify({"errors": ["name must be unique"]}), 400
            row = _db.get_source(conn, sid)
        finally:
            conn.close()
        return jsonify({"source": dict(row)}), 201

    @app.route("/api/sources/<int:source_id>", methods=["PATCH"])
    def api_sources_update(source_id: int):
        from its_briefing import db as _db
        payload = request.get_json(silent=True) or {}
        data, errors = _validate_source_payload(payload, partial=True)
        if errors:
            return jsonify({"errors": errors}), 400
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            if _db.get_source(conn, source_id) is None:
                return jsonify({"errors": ["not found"]}), 404
            try:
                _db.update_source(conn, source_id, data)
            except sqlite3.IntegrityError:
                return jsonify({"errors": ["name must be unique"]}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/sources/<int:source_id>", methods=["DELETE"])
    def api_sources_delete(source_id: int):
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            _db.delete_source(conn, source_id)
        finally:
            conn.close()
        return ("", 204)

    @app.route("/api/sources", methods=["GET"])
    def api_sources_list():
        from its_briefing import db as _db
        conn = _db.get_connection()
        try:
            _db.init_schema(conn)
            rows = _db.list_sources(conn)
        finally:
            conn.close()
        items = [
            {
                "id": r["id"],
                "name": r["name"],
                "url": r["url"],
                "lang": r["lang"],
                "enabled": bool(r["enabled"]),
                "last_status": r["last_status"],
                "last_checked_at": r["last_checked_at"],
                "last_error": r["last_error"],
                "last_diagnosis": r["last_diagnosis"],
            }
            for r in rows
        ]
        return jsonify({"sources": items})

    return app
