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


def test_post_settings_updates_db_and_redirects(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "ollama",
            "llm_base_url": "http://localhost:11434",
            "llm_model": "llama3.1:8b",
            "schedule_hour": "9",
            "schedule_minute": "0",
            "timezone": "Europe/Berlin",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/settings" in r.headers["Location"]

    follow = client.get(r.headers["Location"])
    body = follow.get_data(as_text=True)
    assert "ollama" in body
    assert "llama3.1:8b" in body


def test_post_settings_rejects_bad_provider(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "bogus",
            "llm_base_url": "http://x",
            "llm_model": "x",
            "schedule_hour": "0",
            "schedule_minute": "0",
            "timezone": "UTC",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "provider" in r.get_data(as_text=True).lower()


def test_post_settings_rejects_bad_timezone(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "ollama",
            "llm_base_url": "http://x",
            "llm_model": "x",
            "schedule_hour": "0",
            "schedule_minute": "0",
            "timezone": "Not/AReal_Zone",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_post_settings_rejects_out_of_range_hour(client):
    r = client.post(
        "/settings",
        data={
            "llm_provider": "ollama",
            "llm_base_url": "http://x",
            "llm_model": "x",
            "schedule_hour": "99",
            "schedule_minute": "0",
            "timezone": "UTC",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
