"""
Tiny JSONL-backed feedback memory. Hermes reads recent records to inform
its next decision (self-supervised improvement loop).
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import List

from config import settings
from core.types import CycleResult, FeedbackRecord

_LOCK = Lock()
_FEEDBACK_PATH: Path = settings.data_dir / "feedback.jsonl"
_CYCLES_PATH: Path = settings.data_dir / "cycles.jsonl"


def append_feedback(record: FeedbackRecord) -> None:
    with _LOCK, _FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")


def load_recent_feedback(limit: int = 50) -> List[FeedbackRecord]:
    if not _FEEDBACK_PATH.exists():
        return []
    with _LOCK, _FEEDBACK_PATH.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()[-limit:]
    out: List[FeedbackRecord] = []
    for ln in lines:
        try:
            out.append(FeedbackRecord.model_validate_json(ln))
        except Exception:
            continue
    return out


def append_cycle(cycle: CycleResult) -> None:
    with _LOCK, _CYCLES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(cycle.model_dump_json() + "\n")


def load_recent_cycles(limit: int = 20) -> List[dict]:
    if not _CYCLES_PATH.exists():
        return []
    with _LOCK, _CYCLES_PATH.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()[-limit:]
    out: List[dict] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def hit_rate(records: List[FeedbackRecord]) -> float:
    if not records:
        return 0.0
    return sum(1 for r in records if r.correct) / len(records)


def total_pnl(records: List[FeedbackRecord]) -> float:
    return sum(r.pnl_usd for r in records)
