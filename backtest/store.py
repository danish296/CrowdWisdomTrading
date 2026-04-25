"""
Persistence for backtest runs.

Each result is written as a single JSON file under data/backtests/<id>.json.
A summary index is rebuilt on every read so the dashboard can list past
runs without parsing the full payloads. All NaN/Inf values are normalised
to None on the way out so Starlette's strict JSON encoder is happy.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings


BASE = settings.data_dir / "backtests"
BASE.mkdir(parents=True, exist_ok=True)


@dataclass
class BacktestRecord:
    id: str
    spec: Dict
    summary: Dict
    started_at: float
    finished_at: float


def save_backtest(payload: Dict) -> Path:
    bid = payload["id"]
    cleaned = _sanitize(payload)
    path = BASE / f"{bid}.json"
    path.write_text(
        json.dumps(cleaned, indent=2, default=_json_default, allow_nan=False),
        encoding="utf-8",
    )
    return path


def list_backtests() -> List[Dict]:
    out: List[Dict] = []
    for p in sorted(BASE.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append(_sanitize({
            "id": payload.get("id", p.stem),
            "spec": payload.get("spec", {}),
            "summary_per_model": payload.get("summary_per_model", {}),
            "pairwise": payload.get("pairwise", []),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "elapsed_s": payload.get("elapsed_s"),
            "n_bars": payload.get("n_bars"),
            "started_at_iso": payload.get("started_at_iso"),
            "finished_at_iso": payload.get("finished_at_iso"),
        }))
    return out


def load_backtest(bid: str) -> Optional[Dict]:
    path = BASE / f"{bid}.json"
    if not path.exists():
        return None
    try:
        return _sanitize(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


def delete_backtest(bid: str) -> bool:
    path = BASE / f"{bid}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


def _json_default(o):
    if isinstance(o, float) and not math.isfinite(o):
        return None
    try:
        return o.__dict__
    except AttributeError:
        return str(o)


def _sanitize(value: Any) -> Any:
    """Recursively turn NaN/Inf floats into None so the result is strict-JSON
    compliant. Lists/dicts/tuples are walked; dataclasses pass through via
    `__dict__` from the JSON encoder."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value
