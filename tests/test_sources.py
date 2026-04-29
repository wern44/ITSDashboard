"""Tests for its_briefing.sources."""
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from its_briefing.config import Source
from its_briefing.sources import HealthResult, health_check_one


def _src(url: str = "https://example.com/feed") -> Source:
    return Source(name="Test", url=url, lang="EN")


def _valid_atom() -> bytes:
    return b"""<?xml version='1.0'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <title>T</title>
  <entry>
    <title>e</title>
    <link href='https://example.com/a'/>
    <id>1</id>
    <updated>2026-04-29T10:00:00Z</updated>
  </entry>
</feed>"""


def test_health_check_one_ok_with_entries(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://example.com/feed", content=_valid_atom())
    result = health_check_one(_src())
    assert isinstance(result, HealthResult)
    assert result.status == "ok"
    assert result.error is None


def test_health_check_one_failed_on_5xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url="https://example.com/feed", status_code=503)
    result = health_check_one(_src())
    assert result.status == "failed"
    assert "503" in result.error


def test_health_check_one_failed_on_zero_entries(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://example.com/feed",
        content=b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>T</title></feed>",
    )
    result = health_check_one(_src())
    assert result.status == "failed"
    assert "no entries" in result.error.lower()


def test_health_check_one_failed_on_timeout(httpx_mock: HTTPXMock):
    import httpx
    httpx_mock.add_exception(httpx.ConnectTimeout("timeout"), url="https://example.com/feed")
    result = health_check_one(_src())
    assert result.status == "failed"
    assert "connection" in result.error.lower() or "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# Task 13: background job registry
# ---------------------------------------------------------------------------

import time
from pathlib import Path

from its_briefing.db import create_source, get_connection, init_schema, list_sources
from its_briefing.sources import (
    get_check_job,
    start_health_check_job,
)


def test_start_health_check_job_returns_job_id(tmp_path: Path, monkeypatch, httpx_mock: HTTPXMock) -> None:
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    conn = get_connection(db_path)
    init_schema(conn)
    create_source(conn, name="Good", url="https://good/", lang="EN", enabled=True)
    conn.close()

    httpx_mock.add_response(url="https://good/", content=_valid_atom())

    job_id = start_health_check_job(source_ids=None)  # None = all
    assert isinstance(job_id, str) and len(job_id) >= 8

    # Wait briefly for the background thread.
    for _ in range(50):
        job = get_check_job(job_id)
        if job["state"] == "done":
            break
        time.sleep(0.05)
    job = get_check_job(job_id)
    assert job["state"] == "done"


def test_health_check_persists_results(tmp_path: Path, monkeypatch, httpx_mock: HTTPXMock) -> None:
    db_path = tmp_path / "t.db"
    monkeypatch.setattr("its_briefing.db.DEFAULT_DB_PATH", db_path)
    conn = get_connection(db_path)
    init_schema(conn)
    sid = create_source(conn, name="Good", url="https://good/", lang="EN", enabled=True)
    conn.close()

    httpx_mock.add_response(url="https://good/", content=_valid_atom())
    job_id = start_health_check_job(source_ids=[sid])
    for _ in range(50):
        if get_check_job(job_id)["state"] == "done":
            break
        time.sleep(0.05)
    conn = get_connection(db_path)
    row = conn.execute("SELECT last_status, last_error FROM sources WHERE id = ?", (sid,)).fetchone()
    conn.close()
    assert row["last_status"] == "ok"
    assert row["last_error"] is None


def test_get_check_job_returns_none_for_unknown_id() -> None:
    assert get_check_job("does-not-exist") is None


# ---------------------------------------------------------------------------
# Task 14: diagnose_failure
# ---------------------------------------------------------------------------

from its_briefing.sources import diagnose_failure
from its_briefing.config import Settings


def _settings() -> Settings:
    return Settings(
        llm_provider="ollama",
        llm_base_url="http://localhost:11434",
        llm_model="x",
        timezone="UTC",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


def test_diagnose_failure_returns_suggestion(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"likely_cause":"throttling","suggested_fix":"retry"}'}},
    )
    suggestion, error = diagnose_failure(
        source_name="X", url="https://x/", last_error="HTTP 503", settings=_settings()
    )
    assert error is None
    assert "throttling" in suggestion
    assert "retry" in suggestion


def test_diagnose_failure_returns_error_when_llm_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat", status_code=500, text="boom"
    )
    suggestion, error = diagnose_failure(
        source_name="X", url="https://x/", last_error="HTTP 503", settings=_settings()
    )
    assert suggestion is None
    assert error is not None
