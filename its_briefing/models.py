"""Pydantic data models for ITS-Briefing."""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class Article(BaseModel):
    """A single article fetched from an RSS feed."""

    id: str
    source: str
    source_lang: str  # "EN" | "DE"
    title: str
    link: str
    published: datetime  # UTC
    summary: str
    category: Optional[str] = None

    @staticmethod
    def make_id(link: str) -> str:
        """Stable 16-char hex id derived from the article link."""
        return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


class Bullet(BaseModel):
    """A single bullet point in the executive summary."""

    text: str
    article_ids: list[str] = Field(default_factory=list)


class ExecutiveSummary(BaseModel):
    """Structured four-section executive summary."""

    critical_vulnerabilities: list[Bullet] = Field(default_factory=list)
    active_threats: list[Bullet] = Field(default_factory=list)
    notable_incidents: list[Bullet] = Field(default_factory=list)
    strategic_policy: list[Bullet] = Field(default_factory=list)

    @classmethod
    def placeholder(cls, target_date: date) -> "ExecutiveSummary":
        """Fallback summary when the LLM is unavailable."""
        msg = f"AI summary unavailable for {target_date.isoformat()} \u2014 see articles below."
        return cls(critical_vulnerabilities=[Bullet(text=msg)])


class Briefing(BaseModel):
    """A complete daily briefing \u2014 one JSON file per day."""

    date: date
    generated_at: datetime  # UTC
    summary: ExecutiveSummary
    articles: list[Article]
    failed_sources: list[str] = Field(default_factory=list)
    article_count: int
