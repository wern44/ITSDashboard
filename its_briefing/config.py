"""Configuration loading for ITS-Briefing."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class Source(BaseModel):
    name: str
    url: str
    lang: str  # "EN" | "DE"


class Category(BaseModel):
    name: str
    description: str
    color: str = "#94a3b8"


class Settings(BaseModel):
    ollama_base_url: str
    ollama_model: str
    timezone: str
    schedule_hour: int
    schedule_minute: int
    flask_host: str
    flask_port: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
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
