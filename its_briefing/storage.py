"""Persist briefings via SQLite. Public API kept stable for app.py / generate.py."""
from __future__ import annotations

from datetime import date as date_type
from pathlib import Path
from typing import Optional

from its_briefing import db
from its_briefing.models import Briefing


def save_briefing(briefing: Briefing, db_path: Optional[Path] = None) -> None:
    """Persist a briefing to the SQLite database."""
    conn = db.get_connection(db_path)
    try:
        db.init_schema(conn)
        db.save_briefing(conn, briefing)
    finally:
        conn.close()


def load_briefing(
    target_date: date_type, db_path: Optional[Path] = None
) -> Optional[Briefing]:
    """Return the briefing for a specific date, or None if absent."""
    conn = db.get_connection(db_path)
    try:
        db.init_schema(conn)
        return db.load_briefing(conn, target_date)
    finally:
        conn.close()


def latest_briefing(db_path: Optional[Path] = None) -> Optional[Briefing]:
    """Return the most-recent briefing, or None if no briefings exist."""
    conn = db.get_connection(db_path)
    try:
        db.init_schema(conn)
        return db.latest_briefing(conn)
    finally:
        conn.close()
