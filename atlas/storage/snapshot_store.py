"""Persistence layer: store/retrieve DailyReport as JSON in SQLite.

Single table ``snapshots(date TEXT PRIMARY KEY, payload TEXT)`` keyed by the
report's ISO date. Payloads are ``DailyReport.to_dict()`` round-tripped through
``json``. All reads degrade gracefully to ``None`` / ``[]`` when the database
or a requested row is missing.
"""
from __future__ import annotations

import json
import os
import sqlite3

from .. import config
from ..types import DailyReport

_CREATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS snapshots ("
    "date TEXT PRIMARY KEY, payload TEXT)"
)


def _ensure_parent_dir(db_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _connect(db_path: str) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_TABLE)
    return conn


def save_report(report: DailyReport, db_path: str = config.DEFAULT_DB) -> None:
    """Upsert ``report`` keyed by ``report.date`` (INSERT OR REPLACE)."""
    payload = json.dumps(report.to_dict(), ensure_ascii=False)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (date, payload) VALUES (?, ?)",
            (report.date, payload),
        )
        conn.commit()
    finally:
        conn.close()


def _query_reports(
    db_path: str, sql: str, params: tuple, *, many: bool
) -> list[DailyReport]:
    """Run ``sql`` (yielding payload rows) and decode into DailyReports.

    Returns ``[]`` when the DB file does not exist. When ``many`` is False the
    caller still receives a list (0 or 1 element) for uniform handling.
    """
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if many else (cur.fetchone(),)
    finally:
        conn.close()
    reports: list[DailyReport] = []
    for row in rows:
        if row is None:
            continue
        reports.append(DailyReport.from_dict(json.loads(row[0])))
    return reports


def load_report(date: str, db_path: str = config.DEFAULT_DB) -> DailyReport | None:
    """Return the report stored for ``date`` or ``None`` if absent."""
    reports = _query_reports(
        db_path,
        "SELECT payload FROM snapshots WHERE date = ?",
        (date,),
        many=False,
    )
    return reports[0] if reports else None


def load_previous(
    before_date: str, db_path: str = config.DEFAULT_DB
) -> DailyReport | None:
    """Return the most recent report strictly before ``before_date``."""
    reports = _query_reports(
        db_path,
        "SELECT payload FROM snapshots WHERE date < ? ORDER BY date DESC LIMIT 1",
        (before_date,),
        many=False,
    )
    return reports[0] if reports else None


def load_recent(
    before_date: str, limit: int, db_path: str = config.DEFAULT_DB
) -> list[DailyReport]:
    """Return up to ``limit`` reports before ``before_date``, most-recent-first."""
    return _query_reports(
        db_path,
        "SELECT payload FROM snapshots WHERE date < ? ORDER BY date DESC LIMIT ?",
        (before_date, limit),
        many=True,
    )
