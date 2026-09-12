"""Application service for parsing and importing one raw JCC revision."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.jcc_data.adapters import build_structured_snapshot
from app.jcc_data.repository import ImportResult, import_snapshot
from app.jcc_data.snapshot_reader import read_snapshot


def import_raw_snapshot(
    db: Session,
    directory: Path,
    *,
    expected_mode: str = "18",
    expected_mode_name: str = "自然之力",
) -> ImportResult:
    raw = read_snapshot(
        directory,
        expected_mode=expected_mode,
        expected_mode_name=expected_mode_name,
    )
    structured = build_structured_snapshot(raw)
    return import_snapshot(db, structured)
