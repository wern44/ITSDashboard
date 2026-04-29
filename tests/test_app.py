"""Tests for app.py routes."""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

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


def test_test_connection_ollama_success(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/api/tags",
        json={"models": [{"name": "llama3.1:8b"}, {"name": "mistral:7b"}]},
    )
    r = client.post(
        "/api/test-connection",
        json={"provider": "ollama", "base_url": "http://localhost:11434", "model": "llama3.1:8b"},
    )
    j = r.get_json()
    assert j["ok"] is True
    assert j["models"] == ["llama3.1:8b", "mistral:7b"]
    assert isinstance(j["latency_ms"], int)


def test_test_connection_lmstudio_success(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:1234/v1/models",
        json={"data": [{"id": "google/gemma-4-26b-a4b"}]},
    )
    r = client.post(
        "/api/test-connection",
        json={"provider": "lmstudio", "base_url": "http://localhost:1234", "model": "google/gemma-4-26b-a4b"},
    )
    j = r.get_json()
    assert j["ok"] is True
    assert j["models"] == ["google/gemma-4-26b-a4b"]


def test_test_connection_failure_returns_error_payload(client, httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="http://localhost:11434/api/tags", status_code=500)
    r = client.post(
        "/api/test-connection",
        json={"provider": "ollama", "base_url": "http://localhost:11434", "model": "x"},
    )
    j = r.get_json()
    assert j["ok"] is False
    assert j["error"]
    assert j["models"] == []


def test_test_connection_rejects_bad_provider(client):
    r = client.post(
        "/api/test-connection",
        json={"provider": "bogus", "base_url": "http://x", "model": "y"},
    )
    j = r.get_json()
    assert j["ok"] is False
    assert "provider" in j["error"].lower()


def test_get_sources_page(client):
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert b"Sources" in resp.data


def test_get_api_sources_returns_list(client):
    from its_briefing import db as _db
    conn = _db.get_connection()
    try:
        _db.create_source(conn, name="Z", url="https://z/", lang="EN", enabled=True)
    finally:
        conn.close()
    resp = client.get("/api/sources")
    assert resp.status_code == 200
    items = resp.get_json()["sources"]
    assert any(s["name"] == "Z" for s in items)


def test_post_api_sources_creates(client):
    resp = client.post(
        "/api/sources",
        json={"name": "New", "url": "https://n/", "lang": "EN", "enabled": True},
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["source"]["name"] == "New"
    assert body["source"]["id"] >= 1


def test_post_api_sources_validation_errors(client):
    resp = client.post("/api/sources", json={"name": "", "url": "", "lang": "FR"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["errors"]


def test_post_api_sources_duplicate_name(client):
    client.post("/api/sources", json={"name": "Dup", "url": "https://a/", "lang": "EN"})
    resp = client.post("/api/sources", json={"name": "Dup", "url": "https://b/", "lang": "EN"})
    assert resp.status_code == 400


def test_patch_api_sources(client):
    resp = client.post("/api/sources", json={"name": "X", "url": "https://x/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    resp = client.patch(f"/api/sources/{sid}", json={"enabled": False})
    assert resp.status_code == 200
    resp = client.get("/api/sources")
    item = next(s for s in resp.get_json()["sources"] if s["id"] == sid)
    assert item["enabled"] is False


def test_delete_api_sources(client):
    resp = client.post("/api/sources", json={"name": "Y", "url": "https://y/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    resp = client.delete(f"/api/sources/{sid}")
    assert resp.status_code == 204
    resp = client.get("/api/sources")
    assert not any(s["id"] == sid for s in resp.get_json()["sources"])


def test_post_check_returns_job_id(client, monkeypatch):
    resp = client.post("/api/sources", json={"name": "S", "url": "https://s/", "lang": "EN"})
    sid = resp.get_json()["source"]["id"]
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "start_health_check_job", lambda source_ids: "fake-job-id")

    resp = client.post(f"/api/sources/{sid}/check")
    assert resp.status_code == 202
    assert resp.get_json()["job_id"] == "fake-job-id"


def test_post_check_all_returns_job_id(client, monkeypatch):
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "start_health_check_job", lambda source_ids: "fake-all")
    resp = client.post("/api/sources/check-all")
    assert resp.status_code == 202
    assert resp.get_json()["job_id"] == "fake-all"


def test_get_check_status(client, monkeypatch):
    from its_briefing import sources as src_mod
    monkeypatch.setattr(
        src_mod, "get_check_job",
        lambda jid: {"state": "done", "results": {1: {"status": "ok", "error": None}}} if jid == "j1" else None,
    )
    resp = client.get("/api/sources/check-status?job_id=j1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"] == "done"


def test_get_check_status_unknown_job(client, monkeypatch):
    from its_briefing import sources as src_mod
    monkeypatch.setattr(src_mod, "get_check_job", lambda jid: None)
    resp = client.get("/api/sources/check-status?job_id=nope")
    assert resp.status_code == 404
