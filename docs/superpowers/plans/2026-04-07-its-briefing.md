# ITS-Briefing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a slim, standalone Python web app that fetches 19 cybersecurity RSS feeds daily, classifies the articles via local Ollama, generates a structured AI executive summary, and serves the result on a dark-mode Flask page.

**Architecture:** A single Python process running Flask (UI) + APScheduler (06:00 Europe/Berlin daily job) in-process. Pipeline modules (fetch / llm / storage) are pure I/O with no Flask dependency. Generated briefings are stored as one JSON file per day in `cache/`. No database.

**Tech Stack:** Python 3.11+, Flask 3, feedparser, httpx, APScheduler, Pydantic 2, PyYAML, python-dotenv. Tailwind via CDN. pytest + pytest-httpx + freezegun for tests.

**Spec:** `docs/superpowers/specs/2026-04-07-its-briefing-design.md`

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `its_briefing/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/.gitkeep`
- Create: `config/.gitkeep`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "its-briefing"
version = "0.1.0"
description = "Daily AI-curated cybersecurity briefing"
requires-python = ">=3.11"
dependencies = [
    "flask>=3.0",
    "feedparser>=6.0",
    "httpx>=0.27",
    "apscheduler>=3.10",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-httpx>=0.30",
    "freezegun>=1.4",
]

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["its_briefing*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
build/
dist/

# Environment
.env

# Cache (runtime briefings)
cache/

# IDE
.vscode/
.idea/
.DS_Store

# Pytest
.pytest_cache/
```

- [ ] **Step 3: Create `.env.example`**

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
TIMEZONE=Europe/Berlin
SCHEDULE_HOUR=6
SCHEDULE_MINUTE=0
FLASK_HOST=127.0.0.1
FLASK_PORT=8089
LOG_LEVEL=INFO
```

- [ ] **Step 4: Create empty `its_briefing/__init__.py`**

```python
"""ITS-Briefing — daily AI-curated cybersecurity briefing."""
```

- [ ] **Step 5: Create empty `tests/__init__.py`**

Empty file (just needs to exist).

- [ ] **Step 6: Create placeholder `README.md`**

```markdown
# ITS-Briefing

Daily AI-curated cybersecurity briefing — slim Python app that aggregates 19 RSS feeds, classifies articles via local Ollama, and serves a structured executive summary on a dark-mode web page.

Full README will be filled in by the last task.
```

- [ ] **Step 7: Create `config/.gitkeep` and `tests/fixtures/.gitkeep`**

Empty files so the directories exist before YAML/fixture files are added.

- [ ] **Step 8: Init git, install dependencies, verify import**

```bash
git init
git add .
git commit -m "chore: project scaffolding"
python -m venv .venv
source .venv/Scripts/activate  # Git Bash on Windows
pip install -e ".[dev]"
python -c "import its_briefing; print('ok')"
```

Expected output: `ok`

---

## Task 2: Configuration files (sources + categories YAML)

**Files:**
- Create: `config/sources.yaml`
- Create: `config/categories.yaml`

- [ ] **Step 1: Create `config/sources.yaml`**

```yaml
sources:
  - name: "Bleeping Computer"
    url: "https://www.bleepingcomputer.com/feed/"
    lang: "EN"
  - name: "The Hacker News"
    url: "https://feeds.feedburner.com/TheHackersNews"
    lang: "EN"
  - name: "Dark Reading"
    url: "https://www.darkreading.com/rss.xml"
    lang: "EN"
  - name: "SecurityWeek"
    url: "https://feeds.feedburner.com/securityweek"
    lang: "EN"
  - name: "The Record"
    url: "https://therecord.media/feed"
    lang: "EN"
  - name: "Help Net Security"
    url: "https://www.helpnetsecurity.com/feed/"
    lang: "EN"
  - name: "Krebs on Security"
    url: "https://krebsonsecurity.com/feed/"
    lang: "EN"
  - name: "Schneier on Security"
    url: "https://www.schneier.com/feed/"
    lang: "EN"
  - name: "Graham Cluley"
    url: "https://grahamcluley.com/feed/"
    lang: "EN"
  - name: "Risky Business News"
    url: "https://risky.biz/feeds/risky-business-news"
    lang: "EN"
  - name: "Google Threat Intelligence"
    url: "https://feeds.feedburner.com/threatintelligence/pvexyqv7v0v"
    lang: "EN"
  - name: "CrowdStrike"
    url: "https://www.crowdstrike.com/blog/feed/"
    lang: "EN"
  - name: "Cisco Talos"
    url: "https://blog.talosintelligence.com/feeds/posts/default"
    lang: "EN"
  - name: "Sophos News"
    url: "https://news.sophos.com/en-us/feed/"
    lang: "EN"
  - name: "SANS ISC"
    url: "https://isc.sans.edu/rssfeed_full.xml"
    lang: "EN"
  - name: "heise Security"
    url: "https://www.heise.de/security/rss/alert-news-atom.xml"
    lang: "DE"
  - name: "golem.de Security"
    url: "https://rss.golem.de/rss.php?ms=security&feed=RSS1.0"
    lang: "DE"
  - name: "BSI / CERT-Bund WID"
    url: "https://www.bsi.bund.de/SiteGlobals/Functions/RSSFeed/RSSNewsfeed/RSSNewsfeed_WID.xml"
    lang: "DE"
  - name: "Borns IT-Blog"
    url: "https://www.borncity.com/blog/feed/"
    lang: "DE"
```

- [ ] **Step 2: Create `config/categories.yaml`**

```yaml
categories:
  - name: "IT News daily"
    description: "General IT and tech industry news that is not security-specific"
    color: "#94a3b8"
  - name: "IT-Security"
    description: "Defensive security topics: best practices, frameworks, hardening, security operations"
    color: "#22d3ee"
  - name: "Cyber-Security"
    description: "Broader cyber news: industry, policy, geopolitics, nation-state activity"
    color: "#a78bfa"
  - name: "Threats and Vulnerabilities"
    description: "CVEs, advisories, vulnerable software, patches"
    color: "#f59e0b"
  - name: "Hacks"
    description: "Confirmed breaches, ransomware incidents, data leaks"
    color: "#ef4444"
  - name: "0-Day"
    description: "Zero-day vulnerabilities and exploits actively used in the wild"
    color: "#dc2626"
  - name: "Regulation"
    description: "Compliance, NIS2, DORA, GDPR, government policy and law"
    color: "#10b981"
```

- [ ] **Step 3: Commit**

```bash
git add config/
git commit -m "feat: add sources and categories config"
```

---

## Task 3: Pydantic data models

**Files:**
- Create: `its_briefing/models.py`

- [ ] **Step 1: Write `its_briefing/models.py`**

```python
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
        msg = f"AI summary unavailable for {target_date.isoformat()} — see articles below."
        return cls(critical_vulnerabilities=[Bullet(text=msg)])


class Briefing(BaseModel):
    """A complete daily briefing — one JSON file per day."""

    date: date
    generated_at: datetime  # UTC
    summary: ExecutiveSummary
    articles: list[Article]
    failed_sources: list[str] = Field(default_factory=list)
    article_count: int
```

- [ ] **Step 2: Verify the module imports**

```bash
python -c "from its_briefing.models import Article, Briefing, ExecutiveSummary, Bullet; print('ok')"
```

Expected output: `ok`

- [ ] **Step 3: Verify `Article.make_id` is stable**

```bash
python -c "from its_briefing.models import Article; print(Article.make_id('https://example.com/a'))"
```

Expected output: a 16-character hex string (same on every run).

- [ ] **Step 4: Commit**

```bash
git add its_briefing/models.py
git commit -m "feat: add pydantic data models"
```

---

## Task 4: Config loader (settings + sources + categories)

**Files:**
- Create: `its_briefing/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
"""Tests for its_briefing.config."""
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'its_briefing.config'`.

- [ ] **Step 3: Implement `its_briefing/config.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Verify the real config files load**

```bash
python -c "from its_briefing.config import load_sources, load_categories; print(len(load_sources()), len(load_categories()))"
```

Expected output: `19 7`

- [ ] **Step 6: Commit**

```bash
git add its_briefing/config.py tests/test_config.py
git commit -m "feat: config loader with yaml + env support"
```

---

## Task 5: Storage module (JSON briefings on disk)

**Files:**
- Create: `its_briefing/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

`tests/test_storage.py`:

```python
"""Tests for its_briefing.storage."""
from datetime import date, datetime, timezone
from pathlib import Path

from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary
from its_briefing.storage import latest_briefing, load_briefing, save_briefing


def _make_briefing(d: date, link: str = "https://example.com/a") -> Briefing:
    article = Article(
        id=Article.make_id(link),
        source="Test Feed",
        source_lang="EN",
        title="Title",
        link=link,
        published=datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc),
        summary="summary",
        category="IT-Security",
    )
    return Briefing(
        date=d,
        generated_at=datetime(2026, 4, 7, 6, 0, 0, tzinfo=timezone.utc),
        summary=ExecutiveSummary(
            critical_vulnerabilities=[Bullet(text="bullet", article_ids=[article.id])]
        ),
        articles=[article],
        failed_sources=[],
        article_count=1,
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    briefing = _make_briefing(date(2026, 4, 7))
    save_briefing(briefing, cache_dir=tmp_path)

    loaded = load_briefing(date(2026, 4, 7), cache_dir=tmp_path)

    assert loaded is not None
    assert loaded.date == date(2026, 4, 7)
    assert loaded.article_count == 1
    assert loaded.articles[0].title == "Title"


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_briefing(date(2026, 4, 7), cache_dir=tmp_path) is None


def test_latest_briefing_picks_highest_date(tmp_path: Path) -> None:
    save_briefing(_make_briefing(date(2026, 4, 5)), cache_dir=tmp_path)
    save_briefing(_make_briefing(date(2026, 4, 7)), cache_dir=tmp_path)
    save_briefing(_make_briefing(date(2026, 4, 6)), cache_dir=tmp_path)

    latest = latest_briefing(cache_dir=tmp_path)

    assert latest is not None
    assert latest.date == date(2026, 4, 7)


def test_latest_briefing_empty_dir(tmp_path: Path) -> None:
    assert latest_briefing(cache_dir=tmp_path) is None


def test_save_creates_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "cache"
    save_briefing(_make_briefing(date(2026, 4, 7)), cache_dir=target)

    assert (target / "briefing-2026-04-07.json").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_storage.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'its_briefing.storage'`.

- [ ] **Step 3: Implement `its_briefing/storage.py`**

```python
"""Persist briefings as JSON files in cache/."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from its_briefing.models import Briefing

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache"

_FILENAME_RE = re.compile(r"^briefing-(\d{4}-\d{2}-\d{2})\.json$")


def _path_for(target_date: date, cache_dir: Path) -> Path:
    return cache_dir / f"briefing-{target_date.isoformat()}.json"


def save_briefing(briefing: Briefing, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _path_for(briefing.date, cache_dir)
    path.write_text(briefing.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_briefing(target_date: date, cache_dir: Path = DEFAULT_CACHE_DIR) -> Optional[Briefing]:
    path = _path_for(target_date, cache_dir)
    if not path.exists():
        return None
    return Briefing.model_validate_json(path.read_text(encoding="utf-8"))


def latest_briefing(cache_dir: Path = DEFAULT_CACHE_DIR) -> Optional[Briefing]:
    if not cache_dir.exists():
        return None
    candidates = []
    for entry in cache_dir.iterdir():
        match = _FILENAME_RE.match(entry.name)
        if match:
            candidates.append((match.group(1), entry))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    newest_path = candidates[0][1]
    return Briefing.model_validate_json(newest_path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_storage.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/storage.py tests/test_storage.py
git commit -m "feat: json file storage for briefings"
```

---

## Task 6: RSS fetch module — single feed parsing

**Files:**
- Create: `its_briefing/fetch.py` (initial version, single-feed parser)
- Create: `tests/fixtures/sample_feed.xml`
- Create: `tests/test_fetch.py`

- [ ] **Step 1: Create `tests/fixtures/sample_feed.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com/</link>
    <description>Sample feed for tests</description>
    <item>
      <title>Recent article</title>
      <link>https://example.com/recent</link>
      <description>This article is recent.</description>
      <pubDate>Tue, 07 Apr 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Old article</title>
      <link>https://example.com/old</link>
      <description>This article is older than 24h.</description>
      <pubDate>Sat, 04 Apr 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
```

- [ ] **Step 2: Write the failing test**

`tests/test_fetch.py`:

```python
"""Tests for its_briefing.fetch."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from freezegun import freeze_time

from its_briefing.config import Source
from its_briefing.fetch import parse_feed_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


@freeze_time("2026-04-07 12:00:00", tz_offset=0)
def test_parse_feed_returns_only_recent_articles() -> None:
    source = Source(name="Test Feed", url="https://example.com/feed", lang="EN")
    raw = FIXTURE.read_bytes()

    articles = parse_feed_bytes(raw, source, now=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc))

    assert len(articles) == 1
    assert articles[0].title == "Recent article"
    assert articles[0].source == "Test Feed"
    assert articles[0].source_lang == "EN"
    assert articles[0].link == "https://example.com/recent"
    assert articles[0].id == articles[0].make_id("https://example.com/recent")


def test_parse_malformed_feed_returns_empty() -> None:
    source = Source(name="Bad Feed", url="https://example.com/bad", lang="EN")

    articles = parse_feed_bytes(b"<not-xml>", source, now=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc))

    assert articles == []
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
pytest tests/test_fetch.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'its_briefing.fetch'`.

- [ ] **Step 4: Implement the parser in `its_briefing/fetch.py`**

```python
"""Fetch RSS feeds and filter to the last 24 hours."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import httpx

from its_briefing.config import Source
from its_briefing.models import Article

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 10
MAX_WORKERS = 10
WINDOW_HOURS = 24


def _entry_published(entry: dict) -> Optional[datetime]:
    """Extract a UTC datetime from a feedparser entry, or None."""
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            try:
                return datetime(*struct[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def parse_feed_bytes(raw: bytes, source: Source, now: datetime) -> list[Article]:
    """Parse raw feed bytes into Article objects, filtered to the last WINDOW_HOURS."""
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        return []

    cutoff = now - timedelta(hours=WINDOW_HOURS)
    articles: list[Article] = []
    for entry in parsed.entries:
        published = _entry_published(entry)
        if published is None or published < cutoff:
            continue
        link = entry.get("link") or ""
        title = entry.get("title") or ""
        summary = entry.get("summary") or entry.get("description") or ""
        if not link or not title:
            continue
        articles.append(
            Article(
                id=Article.make_id(link),
                source=source.name,
                source_lang=source.lang,
                title=title,
                link=link,
                published=published,
                summary=summary,
                category=None,
            )
        )
    return articles
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
pytest tests/test_fetch.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add its_briefing/fetch.py tests/test_fetch.py tests/fixtures/sample_feed.xml
git commit -m "feat: parse single rss feed with 24h filter"
```

---

## Task 7: RSS fetch module — concurrent fetch_all

**Files:**
- Modify: `its_briefing/fetch.py` (add `fetch_all`)
- Modify: `tests/test_fetch.py` (add tests)

- [ ] **Step 1: Add failing tests to `tests/test_fetch.py`**

Append after the existing tests:

```python
from pytest_httpx import HTTPXMock

from its_briefing.fetch import fetch_all


@freeze_time("2026-04-07 12:00:00", tz_offset=0)
def test_fetch_all_aggregates_articles(httpx_mock: HTTPXMock) -> None:
    raw = FIXTURE.read_bytes()
    httpx_mock.add_response(url="https://a.example/feed", content=raw)
    httpx_mock.add_response(url="https://b.example/feed", content=raw)

    sources = [
        Source(name="A", url="https://a.example/feed", lang="EN"),
        Source(name="B", url="https://b.example/feed", lang="DE"),
    ]
    articles, failed = fetch_all(sources)

    assert failed == []
    assert len(articles) == 2  # one recent article per feed
    assert {a.source for a in articles} == {"A", "B"}


@freeze_time("2026-04-07 12:00:00", tz_offset=0)
def test_fetch_all_records_failures(httpx_mock: HTTPXMock) -> None:
    raw = FIXTURE.read_bytes()
    httpx_mock.add_response(url="https://ok.example/feed", content=raw)
    httpx_mock.add_response(url="https://broken.example/feed", status_code=500)

    sources = [
        Source(name="OK", url="https://ok.example/feed", lang="EN"),
        Source(name="Broken", url="https://broken.example/feed", lang="EN"),
    ]
    articles, failed = fetch_all(sources)

    assert "Broken" in failed
    assert any(a.source == "OK" for a in articles)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_fetch.py -v
```

Expected: 2 failures with `ImportError: cannot import name 'fetch_all'`.

- [ ] **Step 3: Add `fetch_all` to `its_briefing/fetch.py`**

Append at the bottom of the file:

```python
def _fetch_one(client: httpx.Client, source: Source, now: datetime) -> tuple[list[Article], Optional[str]]:
    """Fetch a single feed. Returns (articles, failed_source_name_or_None)."""
    try:
        response = client.get(source.url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Fetch failed for %s: %s", source.name, exc)
        return [], source.name

    try:
        articles = parse_feed_bytes(response.content, source, now=now)
    except Exception as exc:  # noqa: BLE001 — feedparser can raise odd things
        logger.warning("Parse failed for %s: %s", source.name, exc)
        return [], source.name

    return articles, None


def fetch_all(sources: list[Source]) -> tuple[list[Article], list[str]]:
    """Concurrently fetch all sources. Returns (articles, failed_source_names)."""
    now = datetime.now(timezone.utc)
    articles: list[Article] = []
    failed: list[str] = []

    with httpx.Client(headers={"User-Agent": "ITS-Briefing/0.1"}) as client:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_source = {
                executor.submit(_fetch_one, client, source, now): source for source in sources
            }
            for future in as_completed(future_to_source):
                got_articles, failure = future.result()
                articles.extend(got_articles)
                if failure:
                    failed.append(failure)

    articles.sort(key=lambda a: a.published, reverse=True)
    return articles, failed
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_fetch.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/fetch.py tests/test_fetch.py
git commit -m "feat: concurrent fetch_all with failure tracking"
```

---

## Task 8: LLM module — article classification

**Files:**
- Create: `its_briefing/llm.py` (initial version, classify only)
- Create: `tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

`tests/test_llm.py`:

```python
"""Tests for its_briefing.llm."""
from datetime import datetime, timezone

from pytest_httpx import HTTPXMock

from its_briefing.config import Category, Settings
from its_briefing.llm import classify_article
from its_briefing.models import Article


def _article() -> Article:
    return Article(
        id="abc123",
        source="Test",
        source_lang="EN",
        title="Critical zero-day in Foo software",
        link="https://example.com/x",
        published=datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc),
        summary="A new 0day was disclosed.",
    )


def _categories() -> list[Category]:
    return [
        Category(name="0-Day", description="Zero-days"),
        Category(name="Hacks", description="Breaches"),
        Category(name="Regulation", description="Compliance"),
    ]


def _settings() -> Settings:
    return Settings(
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1:8b",
        timezone="Europe/Berlin",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


def test_classify_article_returns_chosen_category(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"category": "0-Day"}'}},
    )

    result = classify_article(_article(), _categories(), _settings())

    assert result == "0-Day"


def test_classify_article_unknown_category_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": '{"category": "Bogus"}'}},
    )

    result = classify_article(_article(), _categories(), _settings())

    assert result == "Uncategorized"


def test_classify_article_invalid_json_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": "this is not json"}},
    )

    result = classify_article(_article(), _categories(), _settings())

    assert result == "Uncategorized"


def test_classify_article_http_error_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://localhost:11434/api/chat", status_code=500)

    result = classify_article(_article(), _categories(), _settings())

    assert result == "Uncategorized"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_llm.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'its_briefing.llm'`.

- [ ] **Step 3: Implement `its_briefing/llm.py`**

```python
"""Local Ollama client: classify articles and build executive summary."""
from __future__ import annotations

import json
import logging
from datetime import date

import httpx
from pydantic import ValidationError

from its_briefing.config import Category, Settings
from its_briefing.models import Article, ExecutiveSummary

logger = logging.getLogger(__name__)

OLLAMA_TIMEOUT_SECONDS = 60
UNCATEGORIZED = "Uncategorized"


def _ollama_chat(prompt: str, settings: Settings) -> str:
    """Call Ollama /api/chat with format=json. Returns the assistant content string."""
    payload = {
        "model": settings.ollama_model,
        "format": "json",
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = httpx.post(
        f"{settings.ollama_base_url}/api/chat",
        json=payload,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    return data["message"]["content"]


def _classification_prompt(article: Article, categories: list[Category]) -> str:
    cat_lines = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    return (
        "You are a cybersecurity news classifier. Pick exactly ONE category for the article.\n\n"
        f"Categories:\n{cat_lines}\n\n"
        f"Article title: {article.title}\n"
        f"Article summary: {article.summary[:500]}\n\n"
        'Respond with JSON only: {"category": "<one of the names above>"}'
    )


def classify_article(article: Article, categories: list[Category], settings: Settings) -> str:
    """Classify a single article into one of the configured categories."""
    valid_names = {c.name for c in categories}
    try:
        content = _ollama_chat(_classification_prompt(article, categories), settings)
        parsed = json.loads(content)
        chosen = parsed.get("category", "")
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Classification failed for %s: %s", article.id, exc)
        return UNCATEGORIZED

    if chosen not in valid_names:
        logger.warning("Classifier returned unknown category %r for %s", chosen, article.id)
        return UNCATEGORIZED
    return chosen
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_llm.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/llm.py tests/test_llm.py
git commit -m "feat: ollama-backed article classification"
```

---

## Task 9: LLM module — executive summary builder

**Files:**
- Modify: `its_briefing/llm.py` (add `build_summary`)
- Modify: `tests/test_llm.py` (add tests)

- [ ] **Step 1: Add failing tests to `tests/test_llm.py`**

Append after the existing tests:

```python
from datetime import date
from its_briefing.llm import build_summary
from its_briefing.models import Bullet, ExecutiveSummary


def _articles() -> list[Article]:
    return [
        Article(
            id="id1",
            source="Test",
            source_lang="EN",
            title="CVE-2026-0001 critical RCE in WidgetServer",
            link="https://example.com/1",
            published=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc),
            summary="A critical RCE was disclosed.",
            category="0-Day",
        ),
        Article(
            id="id2",
            source="Test",
            source_lang="EN",
            title="Ransomware hits hospital chain",
            link="https://example.com/2",
            published=datetime(2026, 4, 7, 8, 0, tzinfo=timezone.utc),
            summary="A ransomware group attacked.",
            category="Hacks",
        ),
    ]


def test_build_summary_parses_structured_response(httpx_mock: HTTPXMock) -> None:
    structured = {
        "critical_vulnerabilities": [
            {"text": "CVE-2026-0001 RCE in WidgetServer", "article_ids": ["id1"]}
        ],
        "active_threats": [],
        "notable_incidents": [
            {"text": "Hospital chain hit by ransomware", "article_ids": ["id2"]}
        ],
        "strategic_policy": [],
    }
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": json.dumps(structured)}},
    )

    summary = build_summary(_articles(), _settings(), target_date=date(2026, 4, 7))

    assert isinstance(summary, ExecutiveSummary)
    assert len(summary.critical_vulnerabilities) == 1
    assert summary.critical_vulnerabilities[0].text.startswith("CVE-2026-0001")
    assert summary.notable_incidents[0].article_ids == ["id2"]


def test_build_summary_invalid_json_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": "garbage"}},
    )
    httpx_mock.add_response(
        url="http://localhost:11434/api/chat",
        json={"message": {"content": "garbage again"}},
    )

    summary = build_summary(_articles(), _settings(), target_date=date(2026, 4, 7))

    assert isinstance(summary, ExecutiveSummary)
    assert summary.critical_vulnerabilities[0].text.startswith("AI summary unavailable")


def test_build_summary_http_error_falls_back(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://localhost:11434/api/chat", status_code=500)
    httpx_mock.add_response(url="http://localhost:11434/api/chat", status_code=500)

    summary = build_summary(_articles(), _settings(), target_date=date(2026, 4, 7))

    assert summary.critical_vulnerabilities[0].text.startswith("AI summary unavailable")
```

You'll also need this import at the top of the test file (add it to the existing imports):

```python
import json
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/test_llm.py -v
```

Expected: 3 failures with `ImportError: cannot import name 'build_summary'`.

- [ ] **Step 3: Add `build_summary` to `its_briefing/llm.py`**

Append at the bottom of the file:

```python
def _summary_prompt(articles: list[Article]) -> str:
    article_lines = []
    for a in articles:
        cat = a.category or "Uncategorized"
        snippet = a.summary[:300].replace("\n", " ")
        article_lines.append(f"[{a.id}] ({cat}) {a.title} — {snippet}")
    article_block = "\n".join(article_lines)
    return (
        "You are a cybersecurity briefing analyst. Read the articles below and produce an "
        "executive summary in four sections.\n\n"
        "Each section is a list of bullets. Each bullet has a short text (1-2 sentences) and a "
        "list of article_ids that support it. Use the bracketed [id] from each article line.\n\n"
        "Sections:\n"
        "- critical_vulnerabilities: CVEs, advisories, urgent patches\n"
        "- active_threats: ongoing campaigns, malware, threat actor activity\n"
        "- notable_incidents: confirmed breaches, ransomware victims, leaks\n"
        "- strategic_policy: regulation, geopolitics, industry trends\n\n"
        "Empty sections are allowed (return an empty list). Be concise.\n\n"
        f"Articles:\n{article_block}\n\n"
        'Respond with JSON only, matching this exact shape:\n'
        '{"critical_vulnerabilities":[{"text":"...","article_ids":["..."]}],'
        '"active_threats":[],"notable_incidents":[],"strategic_policy":[]}'
    )


def _try_build_summary(articles: list[Article], settings: Settings) -> ExecutiveSummary:
    content = _ollama_chat(_summary_prompt(articles), settings)
    parsed = json.loads(content)
    return ExecutiveSummary.model_validate(parsed)


def build_summary(
    articles: list[Article], settings: Settings, target_date: date
) -> ExecutiveSummary:
    """Build the executive summary, with one retry and a placeholder fallback."""
    for attempt in (1, 2):
        try:
            return _try_build_summary(articles, settings)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValidationError) as exc:
            logger.warning("Summary attempt %d failed: %s", attempt, exc)
    return ExecutiveSummary.placeholder(target_date)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_llm.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add its_briefing/llm.py tests/test_llm.py
git commit -m "feat: ollama-backed executive summary builder"
```

---

## Task 10: Pipeline orchestrator (`generate.py`)

**Files:**
- Create: `its_briefing/generate.py`
- Create: `tests/test_generate.py`

- [ ] **Step 1: Write the failing test**

`tests/test_generate.py`:

```python
"""Tests for its_briefing.generate."""
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from its_briefing.config import Category, Settings, Source
from its_briefing.generate import run
from its_briefing.models import Article, Briefing, Bullet, ExecutiveSummary


def _settings() -> Settings:
    return Settings(
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.1:8b",
        timezone="Europe/Berlin",
        schedule_hour=6,
        schedule_minute=0,
        flask_host="127.0.0.1",
        flask_port=8089,
        log_level="INFO",
    )


def _sample_article(idx: int) -> Article:
    return Article(
        id=f"id{idx}",
        source="Test",
        source_lang="EN",
        title=f"Title {idx}",
        link=f"https://example.com/{idx}",
        published=datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc),
        summary="summary",
    )


def _fake_summary() -> ExecutiveSummary:
    return ExecutiveSummary(
        critical_vulnerabilities=[Bullet(text="bullet", article_ids=["id1"])]
    )


@freeze_time("2026-04-07 06:00:00", tz_offset=0)
def test_run_orchestrates_pipeline(tmp_path: Path) -> None:
    sources = [Source(name="Test", url="https://example.com/feed", lang="EN")]
    categories = [Category(name="IT-Security", description="defense")]
    fake_articles = [_sample_article(1), _sample_article(2)]

    with (
        patch("its_briefing.generate.config.load_sources", return_value=sources),
        patch("its_briefing.generate.config.load_categories", return_value=categories),
        patch("its_briefing.generate.config.Settings.from_env", return_value=_settings()),
        patch("its_briefing.generate.fetch.fetch_all", return_value=(fake_articles, ["BadFeed"])),
        patch("its_briefing.generate.llm.classify_article", side_effect=["IT-Security", "IT-Security"]),
        patch("its_briefing.generate.llm.build_summary", return_value=_fake_summary()),
    ):
        briefing = run(cache_dir=tmp_path)

    assert isinstance(briefing, Briefing)
    assert briefing.date == date(2026, 4, 7)
    assert briefing.article_count == 2
    assert briefing.failed_sources == ["BadFeed"]
    assert briefing.articles[0].category == "IT-Security"
    assert (tmp_path / "briefing-2026-04-07.json").exists()


@freeze_time("2026-04-07 06:00:00", tz_offset=0)
def test_run_returns_none_on_unhandled_exception(tmp_path: Path) -> None:
    with patch(
        "its_briefing.generate.config.load_sources", side_effect=RuntimeError("boom")
    ):
        result = run(cache_dir=tmp_path)

    assert result is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_generate.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'its_briefing.generate'`.

- [ ] **Step 3: Implement `its_briefing/generate.py`**

```python
"""Pipeline orchestrator: fetch → classify → summarize → save."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from its_briefing import config, fetch, llm, storage
from its_briefing.models import Briefing

logger = logging.getLogger(__name__)


def run(cache_dir: Optional[Path] = None) -> Optional[Briefing]:
    """Run the full briefing pipeline. Returns the saved Briefing or None on failure."""
    try:
        settings = config.Settings.from_env()
        sources = config.load_sources()
        categories = config.load_categories()

        logger.info("Fetching %d sources…", len(sources))
        articles, failed_sources = fetch.fetch_all(sources)
        logger.info("Fetched %d articles, %d sources failed", len(articles), len(failed_sources))

        for article in articles:
            article.category = llm.classify_article(article, categories, settings)

        now = datetime.now(timezone.utc)
        target_date = now.date()
        summary = llm.build_summary(articles, settings, target_date=target_date)

        briefing = Briefing(
            date=target_date,
            generated_at=now,
            summary=summary,
            articles=articles,
            failed_sources=failed_sources,
            article_count=len(articles),
        )

        if cache_dir is None:
            storage.save_briefing(briefing)
        else:
            storage.save_briefing(briefing, cache_dir=cache_dir)

        logger.info(
            "Briefing for %s generated: %d articles, %d failed sources",
            target_date.isoformat(),
            briefing.article_count,
            len(failed_sources),
        )
        return briefing

    except Exception:  # noqa: BLE001 — top-level guard so the scheduler keeps running
        logger.exception("Briefing generation failed")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_generate.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the full test suite as a sanity check**

```bash
pytest -v
```

Expected: all tests pass (config + storage + fetch + llm + generate ≈ 17 tests).

- [ ] **Step 6: Commit**

```bash
git add its_briefing/generate.py tests/test_generate.py
git commit -m "feat: pipeline orchestrator with top-level error guard"
```

---

## Task 11: APScheduler wrapper

**Files:**
- Create: `its_briefing/scheduler.py`

- [ ] **Step 1: Implement `its_briefing/scheduler.py`**

```python
"""APScheduler wrapper that runs the daily briefing job."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from its_briefing import generate
from its_briefing.config import Settings

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def start(settings: Settings) -> BackgroundScheduler:
    """Start the background scheduler. Idempotent — returns the running instance."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.timezone)
    trigger = CronTrigger(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        timezone=settings.timezone,
    )
    scheduler.add_job(
        generate.run,
        trigger=trigger,
        id="daily_briefing",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started; next run at %s", scheduler.get_job("daily_briefing").next_run_time
    )
    return scheduler


def next_run_time() -> Optional[datetime]:
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_briefing")
    return job.next_run_time if job else None


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
python -c "from its_briefing.scheduler import start, next_run_time, shutdown; print('ok')"
```

Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add its_briefing/scheduler.py
git commit -m "feat: apscheduler wrapper for daily briefing job"
```

---

## Task 12: Flask app and HTML template

**Files:**
- Create: `its_briefing/app.py`
- Create: `its_briefing/templates/briefing.html`

- [ ] **Step 1: Create `its_briefing/templates/briefing.html`**

```html
<!doctype html>
<html lang="en" class="h-full">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ITS Briefing{% if briefing %} — {{ briefing.date.isoformat() }}{% endif %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    html, body { font-family: 'Inter', system-ui, sans-serif; }
    .mono { font-family: 'JetBrains Mono', monospace; }
  </style>
</head>
<body class="h-full bg-slate-900 text-slate-100">
  <div class="max-w-5xl mx-auto px-6 py-10">

    <header class="mb-10 border-b border-slate-800 pb-6">
      <div class="flex items-baseline justify-between flex-wrap gap-4">
        <h1 class="text-3xl font-bold text-cyan-400">ITS Briefing</h1>
        {% if briefing %}
          <div class="text-sm text-slate-400 mono">
            {{ briefing.date.isoformat() }} · generated {{ briefing.generated_at.strftime('%H:%M UTC') }}
          </div>
        {% endif %}
      </div>
      {% if briefing %}
        <div class="mt-4 flex flex-wrap gap-2 text-xs">
          <span class="px-2 py-1 rounded bg-slate-800 text-slate-300">{{ briefing.article_count }} articles</span>
          <span class="px-2 py-1 rounded bg-slate-800 text-slate-300">19 sources</span>
          {% if briefing.failed_sources %}
            <span class="px-2 py-1 rounded bg-red-900/40 text-red-300" title="{{ briefing.failed_sources | join(', ') }}">
              {{ briefing.failed_sources | length }} failed
            </span>
          {% endif %}
        </div>
      {% endif %}
    </header>

    {% if not briefing %}
      <div class="rounded-lg border border-slate-800 bg-slate-800/40 p-8 text-center">
        <p class="text-slate-300 mb-6">Briefing not yet generated.</p>
        <button id="rebuild-btn" class="px-4 py-2 rounded bg-cyan-500 text-slate-900 font-medium hover:bg-cyan-400">
          Generate now
        </button>
      </div>
    {% else %}

      <section class="mb-12 rounded-lg border border-slate-800 bg-slate-800/40 p-6">
        <h2 class="text-xl font-semibold mb-4 text-cyan-300">Executive Summary</h2>

        {% set sections = [
          ('Critical Vulnerabilities', briefing.summary.critical_vulnerabilities),
          ('Active Threats', briefing.summary.active_threats),
          ('Notable Incidents', briefing.summary.notable_incidents),
          ('Strategic / Policy', briefing.summary.strategic_policy),
        ] %}
        {% for title, bullets in sections %}
          <div class="mb-5 last:mb-0">
            <h3 class="text-sm uppercase tracking-wider text-slate-400 mb-2">{{ title }}</h3>
            {% if bullets %}
              <ul class="space-y-2">
                {% for bullet in bullets %}
                  <li class="text-slate-200 leading-relaxed">
                    {{ bullet.text }}
                    {% for aid in bullet.article_ids %}
                      <a href="#article-{{ aid }}" class="text-cyan-400 hover:text-cyan-300 mono text-xs ml-1">[{{ loop.index }}]</a>
                    {% endfor %}
                  </li>
                {% endfor %}
              </ul>
            {% else %}
              <p class="text-slate-500 italic text-sm">No items.</p>
            {% endif %}
          </div>
        {% endfor %}
      </section>

      <section>
        <h2 class="text-xl font-semibold mb-4 text-cyan-300">Articles ({{ briefing.article_count }})</h2>
        <div class="space-y-3">
          {% for article in briefing.articles %}
            <article id="article-{{ article.id }}" class="rounded-lg border border-slate-800 bg-slate-800/30 p-4 hover:border-slate-700">
              <div class="flex items-center gap-2 text-xs mb-2">
                {% set color = category_colors.get(article.category, '#94a3b8') %}
                <span class="px-2 py-0.5 rounded font-medium" style="background-color: {{ color }}20; color: {{ color }};">
                  {{ article.category or 'Uncategorized' }}
                </span>
                <span class="text-slate-400">{{ article.source }}</span>
                <span class="px-1.5 py-0.5 rounded bg-slate-700 text-slate-300 mono text-[10px]">{{ article.source_lang }}</span>
                <span class="text-slate-500 mono ml-auto" title="{{ article.published.isoformat() }}">
                  {{ article.published.strftime('%H:%M UTC') }}
                </span>
              </div>
              <h3 class="text-lg font-semibold mb-1">
                <a href="{{ article.link }}" target="_blank" rel="noopener" class="hover:text-cyan-300">{{ article.title }}</a>
              </h3>
              <p class="text-slate-400 text-sm">{{ article.summary | striptags | truncate(220) }}</p>
            </article>
          {% endfor %}
        </div>
      </section>

    {% endif %}

    <footer class="mt-12 pt-6 border-t border-slate-800 flex justify-between items-center text-xs text-slate-500">
      <a href="/health" class="hover:text-slate-300">/health</a>
      <button id="rebuild-now" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">
        Rebuild now
      </button>
    </footer>
  </div>

  <script>
    async function rebuild(btn) {
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = 'Generating…';
      try {
        const r = await fetch('/generate', { method: 'POST' });
        if (r.ok) {
          window.location.reload();
        } else {
          btn.textContent = 'Failed';
          setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2000);
        }
      } catch (e) {
        btn.textContent = 'Failed';
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 2000);
      }
    }
    document.querySelectorAll('#rebuild-btn, #rebuild-now').forEach(btn => {
      btn.addEventListener('click', () => rebuild(btn));
    });
  </script>
</body>
</html>
```

- [ ] **Step 2: Implement `its_briefing/app.py`**

```python
"""Flask web app for ITS-Briefing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template

from its_briefing import generate, scheduler, storage
from its_briefing.config import Settings, load_categories

logger = logging.getLogger(__name__)


def create_app(settings: Optional[Settings] = None) -> Flask:
    """Application factory."""
    settings = settings or Settings.from_env()
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

    categories = load_categories()
    category_colors = {c.name: c.color for c in categories}

    @app.route("/")
    def index() -> str:
        briefing = storage.latest_briefing()
        return render_template(
            "briefing.html", briefing=briefing, category_colors=category_colors
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

    return app
```

- [ ] **Step 3: Smoke-test the Flask app**

```bash
python -c "from its_briefing.app import create_app; app = create_app(); client = app.test_client(); r = client.get('/'); print(r.status_code)"
```

Expected output: `200`

- [ ] **Step 4: Commit**

```bash
git add its_briefing/app.py its_briefing/templates/briefing.html
git commit -m "feat: flask app + dark-mode jinja template"
```

---

## Task 13: Process entry point (`__main__.py`)

**Files:**
- Create: `its_briefing/__main__.py`

- [ ] **Step 1: Implement `its_briefing/__main__.py`**

```python
"""Process entry point: starts Flask + APScheduler in one process.

Usage:
    python -m its_briefing
"""
from __future__ import annotations

import logging
import signal
import sys

from dotenv import load_dotenv

from its_briefing import scheduler
from its_briefing.app import create_app
from its_briefing.config import Settings


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    scheduler.start(settings)

    def _graceful_exit(signum, frame):  # noqa: ARG001
        logging.info("Shutting down…")
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    app = create_app(settings)
    app.run(host=settings.flask_host, port=settings.flask_port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the entry point loads (without binding the port)**

```bash
python -c "import its_briefing.__main__ as m; print('ok')"
```

Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add its_briefing/__main__.py
git commit -m "feat: process entry point starts flask + scheduler"
```

---

## Task 14: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace `README.md` with the full version**

```markdown
# ITS Briefing

A slim, standalone Python web app that fetches 19 curated cybersecurity RSS feeds, classifies the articles via local Ollama, generates a structured AI executive summary once per day, and serves the result on a dark-mode web page.

No database. No authentication. One process.

## Requirements

- Python 3.11+
- A running [Ollama](https://ollama.com) instance with the model `llama3.1:8b` pulled (or any other model — set `OLLAMA_MODEL` in `.env`)

```bash
ollama pull llama3.1:8b
```

## Install

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# or: source .venv/bin/activate  # Linux/macOS
pip install -e ".[dev]"
cp .env.example .env
```

## Run

Start the web app + daily scheduler (default 06:00 Europe/Berlin):

```bash
python -m its_briefing
```

Open http://127.0.0.1:8089 in your browser. If no briefing has been generated yet, click "Generate now".

Trigger a fresh briefing manually from the CLI:

```bash
python -m its_briefing.generate
```

Or click "Rebuild now" in the footer of the web page.

## Configuration

- **`config/sources.yaml`** — RSS feeds. Add or remove sources here.
- **`config/categories.yaml`** — topic categories used for classification + UI badges. Add a new category by appending an entry; no code change needed.
- **`.env`** — runtime settings (Ollama URL/model, schedule time, Flask host/port, log level).

## Output

Each daily run writes one file to `cache/briefing-YYYY-MM-DD.json`. The web page always serves the most recent successful briefing.

## Tests

```bash
pytest
```
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: write README"
```

---

## Task 15: End-to-end smoke test against real Ollama and real feeds

This task is **manual verification** — no automated test, no commit unless something needed fixing.

**Prereqs:**
- Ollama running locally: `ollama serve` (in another terminal)
- Model pulled: `ollama pull llama3.1:8b`

- [ ] **Step 1: Run the pipeline once via CLI**

```bash
python -m its_briefing.generate
```

Expected:
- Logs show "Fetching 19 sources…"
- Logs show "Fetched N articles, M sources failed"
- Logs show "Briefing for YYYY-MM-DD generated: N articles, M failed sources"
- File `cache/briefing-<today>.json` exists

- [ ] **Step 2: Inspect the JSON**

```bash
python -c "import json; d=json.load(open('cache/briefing-' + __import__('datetime').date.today().isoformat() + '.json')); print('articles:', d['article_count']); print('summary keys:', list(d['summary'].keys())); print('failed:', d['failed_sources'])"
```

Expected: a positive article count, summary contains the four section keys, failed list is short or empty.

- [ ] **Step 3: Start the web app and load the page**

```bash
python -m its_briefing
```

Open http://127.0.0.1:8089

Expected:
- Page renders in dark mode
- Header shows the date and article count
- Executive Summary card with four sections is visible
- Article timeline lists all articles with category badges and source labels
- Footer "Rebuild now" button is present
- `/health` returns JSON with `status: ok` and `next_scheduled_run` set
- APScheduler logged a `next run at ...` line at startup

- [ ] **Step 4: Verify failure resilience**

Stop Ollama (`ollama` process), then run `python -m its_briefing.generate` again.

Expected: pipeline still completes, all articles get `category="Uncategorized"`, the executive summary contains the placeholder `"AI summary unavailable…"` text, and the page still renders.

Restart Ollama before continuing normal use.

- [ ] **Step 5: Run the full automated test suite one last time**

```bash
pytest -v
```

Expected: all tests pass.

---

## Self-Review Checklist (for the plan author)

- All 14 spec sections covered: ✅ purpose, decisions, architecture, modules, models, data flow, config, error handling, frontend, project layout, dependencies, run commands, testing strategy, acceptance criteria.
- Each module from the spec has a task: `models` (T3), `config` (T4), `storage` (T5), `fetch` (T6+T7), `llm` (T8+T9), `generate` (T10), `scheduler` (T11), `app` + template (T12), `__main__` (T13).
- Each task uses TDD where the spec calls for tests; pure-wrapper modules (`scheduler`, `app`, `__main__`) get smoke tests instead, matching the spec's testing strategy section.
- All function signatures introduced in early tasks (`Settings.from_env`, `load_sources`, `load_categories`, `save_briefing`, `load_briefing`, `latest_briefing`, `parse_feed_bytes`, `fetch_all`, `classify_article`, `build_summary`, `run`) are referenced consistently in later tasks.
- Acceptance criteria 1, 2, 3 covered by Task 15 manual verification. Criteria 4 covered by `pytest -v` in Tasks 10 and 15. Criteria 5 covered by Task 12 + 15. Criteria 6 covered by Task 7's failure test + Task 15 step 4. Criteria 7 covered by Task 8/9 fallback tests + Task 15 step 4.
- No placeholders, no "TODO", no "similar to Task N" — every code block is complete.
