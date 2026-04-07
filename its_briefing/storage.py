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
