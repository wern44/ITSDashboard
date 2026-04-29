"""Configuration loading for ITS-Briefing."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    name: str
    url: str
    lang: str  # "EN" | "DE"
    enabled: bool = True
    last_status: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_diagnosis: Optional[str] = None


class Category(BaseModel):
    name: str
    description: str
    color: str = "#94a3b8"


class Settings(BaseModel):
    # Legacy aliases (ollama_base_url / ollama_model) accepted as constructor kwargs
    # for backwards compatibility with older tests / fixtures. Prefer the canonical
    # llm_base_url / llm_model names. Read-side legacy access is provided via the
    # @property shims below.
    model_config = ConfigDict(populate_by_name=True)

    llm_provider: Literal["ollama", "lmstudio"] = "ollama"
    llm_base_url: str = Field(alias="ollama_base_url")
    llm_model: str = Field(alias="ollama_model")
    timezone: str
    schedule_hour: int
    schedule_minute: int
    flask_host: str
    flask_port: int
    log_level: str

    @property
    def ollama_base_url(self) -> str:
        return self.llm_base_url

    @property
    def ollama_model(self) -> str:
        return self.llm_model

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.environ.get("LLM_PROVIDER", "ollama")
        if provider not in ("ollama", "lmstudio"):
            raise ValueError(f"LLM_PROVIDER must be 'ollama' or 'lmstudio', got {provider!r}")
        base_url = (
            os.environ.get("LLM_BASE_URL")
            or os.environ.get("OLLAMA_BASE_URL")
            or "http://localhost:11434"
        )
        model = (
            os.environ.get("LLM_MODEL")
            or os.environ.get("OLLAMA_MODEL")
            or "llama3.1:8b"
        )
        return cls(
            llm_provider=provider,
            llm_base_url=base_url,
            llm_model=model,
            timezone=os.environ.get("TIMEZONE", "Europe/Berlin"),
            schedule_hour=int(os.environ.get("SCHEDULE_HOUR", "6")),
            schedule_minute=int(os.environ.get("SCHEDULE_MINUTE", "0")),
            flask_host=os.environ.get("FLASK_HOST", "127.0.0.1"),
            flask_port=int(os.environ.get("FLASK_PORT", "8089")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES_PATH = PROJECT_ROOT / "config" / "sources.yaml"
DEFAULT_CATEGORIES_PATH = PROJECT_ROOT / "config" / "categories.yaml"


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> list[Source]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Source(**entry) for entry in data["sources"]]


def load_categories(path: Path = DEFAULT_CATEGORIES_PATH) -> list[Category]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Category(**entry) for entry in data["categories"]]
