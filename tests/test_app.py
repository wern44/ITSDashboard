"""Tests for app.py routes."""
from __future__ import annotations

from pathlib import Path

import pytest

from its_briefing import db
from its_briefing.app import create_app
from its_briefing.config import Settings


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)

    conn = db.get_connection(db_path)
    db.init_schema(conn)
    db.seed_settings_from_env(
        conn,
        Settings(
            llm_provider="lmstudio",
            llm_base_url="http://192.168.32.231:1234",
            llm_model="google/gemma-4-26b-a4b",
            timezone="Europe/Berlin",
            schedule_hour=7,
            schedule_minute=30,
            flask_host="127.0.0.1",
            flask_port=8089,
            log_level="INFO",
        ),
    )
    conn.close()

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_get_settings_renders_current_values(client):
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "lmstudio" in body
    assert "192.168.32.231" in body
    assert "google/gemma-4-26b-a4b" in body
    assert "Europe/Berlin" in body
