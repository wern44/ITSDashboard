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
