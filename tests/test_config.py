"""Tests for its_briefing.config."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from its_briefing.config import Category, Settings, Source, load_categories, load_sources


def test_load_sources_parses_yaml(tmp_path: Path) -> None:
    yaml_file = tmp_path / "sources.yaml"
    yaml_file.write_text(
        """
sources:
  - name: "Test Feed"
    url: "https://example.com/feed"
    lang: "EN"
""".strip()
    )

    sources = load_sources(yaml_file)

    assert len(sources) == 1
    assert isinstance(sources[0], Source)
    assert sources[0].name == "Test Feed"
    assert sources[0].url == "https://example.com/feed"
    assert sources[0].lang == "EN"


def test_load_categories_parses_yaml(tmp_path: Path) -> None:
    yaml_file = tmp_path / "categories.yaml"
    yaml_file.write_text(
        """
categories:
  - name: "Hacks"
    description: "Confirmed breaches"
    color: "#ef4444"
""".strip()
    )

    categories = load_categories(yaml_file)

    assert len(categories) == 1
    assert isinstance(categories[0], Category)
    assert categories[0].name == "Hacks"
    assert categories[0].color == "#ef4444"


def test_settings_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("FLASK_PORT", "9000")

    settings = Settings.from_env()

    assert settings.ollama_model == "qwen2.5:7b"
    assert settings.flask_port == 9000


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "TIMEZONE",
        "SCHEDULE_HOUR",
        "SCHEDULE_MINUTE",
        "FLASK_HOST",
        "FLASK_PORT",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings.from_env()

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "llama3.1:8b"
    assert settings.timezone == "Europe/Berlin"
    assert settings.schedule_hour == 6
    assert settings.schedule_minute == 0
    assert settings.flask_host == "127.0.0.1"
    assert settings.flask_port == 8089
    assert settings.log_level == "INFO"


def test_settings_from_env_uses_new_llm_keys(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "lmstudio")
    monkeypatch.setenv("LLM_BASE_URL", "http://192.168.32.231:1234")
    monkeypatch.setenv("LLM_MODEL", "google/gemma-4-26b-a4b")
    s = Settings.from_env()
    assert s.llm_provider == "lmstudio"
    assert s.llm_base_url == "http://192.168.32.231:1234"
    assert s.llm_model == "google/gemma-4-26b-a4b"


def test_settings_from_env_falls_back_to_legacy_ollama_keys(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://legacy:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    s = Settings.from_env()
    assert s.llm_provider == "ollama"  # default
    assert s.llm_base_url == "http://legacy:11434"
    assert s.llm_model == "llama3.1:8b"


def test_settings_provider_must_be_known(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_defaults_llm(monkeypatch):
    for k in ("LLM_PROVIDER","LLM_BASE_URL","LLM_MODEL","OLLAMA_BASE_URL","OLLAMA_MODEL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.llm_provider == "ollama"
    assert s.llm_base_url == "http://localhost:11434"
    assert s.llm_model == "llama3.1:8b"


def test_source_defaults_enabled_true():
    s = Source(name="A", url="https://a/", lang="EN")
    assert s.enabled is True
    assert s.last_status is None
    assert s.last_checked_at is None
    assert s.last_error is None
    assert s.last_diagnosis is None


def test_source_accepts_health_fields():
    s = Source(
        name="A",
        url="https://a/",
        lang="EN",
        enabled=False,
        last_status="failed",
        last_checked_at=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
        last_error="HTTP 503",
        last_diagnosis="Probably transient.",
    )
    assert s.enabled is False
    assert s.last_status == "failed"
