"""
Rich-powered structured logger. Every agent gets its own colour-tagged
namespace, and a JSONL audit trail lands in `logs/audit.jsonl` for
post-hoc analysis and dashboard streaming.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

from config import settings

# --- Force UTF-8 on Windows so the editorial unicode glyphs render. ---
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if isinstance(_stream, io.TextIOWrapper):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

_THEME = Theme(
    {
        "agent.search":   "bold #f5b400",   # amber
        "agent.data":     "bold #4fc3f7",   # cyan
        "agent.predict":  "bold #ba68c8",   # violet
        "agent.risk":     "bold #ef5350",   # red
        "agent.feedback": "bold #66bb6a",   # green
        "agent.orch":     "bold white on #222222",
        "tool":           "italic #9e9e9e",
        "ok":             "bold #66bb6a",
        "warn":           "bold #ffb300",
        "err":            "bold #ef5350",
    }
)
console = Console(
    theme=_THEME,
    highlight=False,
    legacy_windows=False,         # bypass the cp1252 win32 renderer
    force_terminal=True,
    color_system="truecolor",
)

_AUDIT_PATH: Path = settings.logs_dir / "audit.jsonl"

_root = logging.getLogger("cwt")
if not _root.handlers:
    _root.setLevel(logging.INFO)
    handler = RichHandler(
        console=console,
        markup=True,
        rich_tracebacks=True,
        show_path=False,
        show_time=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _root.addHandler(handler)
    _root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return _root.getChild(name)


def audit(event: str, **fields: Any) -> Dict[str, Any]:
    """Append a structured event to the JSONL audit log and return it."""
    record = {"ts": time.time(), "event": event, **fields}
    try:
        with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        print(f"[audit-error] {exc}", file=sys.stderr)
    return record


def tail_audit(limit: int = 200) -> list[Dict[str, Any]]:
    """Return the last `limit` audit records (for the dashboard)."""
    if not _AUDIT_PATH.exists():
        return []
    try:
        with _AUDIT_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
        out: list[Dict[str, Any]] = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []
