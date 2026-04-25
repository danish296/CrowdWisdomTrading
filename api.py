"""
FastAPI dashboard.

Pages
-----
GET /                  HTML dashboard (live desk)
GET /backtest          HTML backtest workbench

Live-desk JSON
--------------
GET  /api/state                        latest cycle + feedback stats + audit tail
GET  /api/run                          trigger a one-shot cycle (synchronous)
GET  /api/predictors                   list predictors and which are installed

Backtest JSON
-------------
POST /api/backtest/run                 start a backtest in the background
GET  /api/backtest/jobs/{job_id}       poll progress
GET  /api/backtest/list                list saved runs (summary cards)
GET  /api/backtest/{run_id}            full payload for one saved run
DELETE /api/backtest/{run_id}          delete a saved run
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from agents.orchestrator import Orchestrator
from backtest import BacktestSpec, list_backtests, load_backtest, run_backtest, save_backtest
from backtest.engine import result_to_payload
from backtest.store import _sanitize, delete_backtest
from config import settings
from core.logger import get_logger, tail_audit
from core.memory import (
    hit_rate,
    load_recent_cycles,
    load_recent_feedback,
    total_pnl,
)
from tools.kronos_predictor import (
    _kronos_available,
    _resolve_device,
    get_runtime_override,
    list_available_predictors,
    resolve_predictor_choice,
    set_runtime_override,
)

ROOT = Path(__file__).resolve().parent
log = get_logger("api")

app = FastAPI(title="CryptoAdsAgents", version="1.1.0")
app.mount("/static", StaticFiles(directory=str(ROOT / "dashboard" / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "dashboard" / "templates"))

_orch: Orchestrator | None = None


def get_orch() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch


# --------------------------------------------------------------------------- #
# Pages                                                                       #
# --------------------------------------------------------------------------- #


def _ui_select_value(effective: str, kronos_size: str) -> str:
    """Map internal `effective` keys to a value that always exists in the
    dropdown. Browsers show a blank closed <select> when .value is set to a
    non-matching option (e.g. PREDICTOR_DEFAULT=kronos with no kronos-*)."""
    e = (effective or "auto").strip().lower()
    if e in ("statistical", "stat", "baseline"):
        return "statistical"
    if e in ("auto", "ensemble"):
        return e
    if e == "kronos":
        ks = (kronos_size or "small").strip().lower()
        if ks in ("mini", "small", "base"):
            return f"kronos-{ks}"
        return "kronos-small"
    if e in ("kronos-mini", "kronos-small", "kronos-base"):
        return e
    return "auto"


def _predictor_status() -> Dict[str, Any]:
    """What model the live desk will actually use right now.

    Includes the env default, the runtime override (if any), what they
    resolve to, whether Kronos is importable, the device, and the menu
    of options the dashboard should render in the switcher.
    """
    env_default = (settings.predictor_default or "auto").strip().lower()
    override = get_runtime_override() or ""
    effective = resolve_predictor_choice() or "auto"
    kronos_ok = _kronos_available()
    size = (settings.kronos_model_size or "small").strip().lower()
    device = _resolve_device()
    select_value = _ui_select_value(effective, size)

    # What the orchestrator will actually call right now, fully resolved.
    if effective in ("statistical", "stat", "baseline"):
        active = "statistical"
        reason = (
            f"override={override}" if override
            else f"PREDICTOR_DEFAULT={env_default} (forced baseline)"
        )
    elif effective == "ensemble":
        if kronos_ok:
            active = f"ensemble:kronos-{size}+statistical"
            reason = (
                "ensemble -> running both engines, averaging P(up)"
                if not override
                else f"override=ensemble (kronos-{size} + statistical)"
            )
        else:
            active = "ensemble:statistical-only"
            reason = "ensemble requested but Kronos isn't installed -- using statistical only"
    elif effective.startswith("kronos"):
        size = effective.split("-", 1)[1] if "-" in effective else size
        active = f"kronos-{size}"
        if not kronos_ok:
            reason = f"override={effective} -- WILL FAIL until Kronos is installed"
        elif override:
            reason = f"override={effective}"
        else:
            reason = f"PREDICTOR_DEFAULT={env_default}"
    else:  # "auto" or unset
        if kronos_ok:
            active = f"kronos-{size}"
            reason = f"auto -> Kronos installed, using size={size}"
        else:
            active = "statistical"
            reason = "auto -> Kronos not installed, falling back to statistical"

    options = [
        {"value": "auto",          "label": "Auto (Kronos if installed)",         "available": True},
        {"value": "statistical",   "label": "Statistical only (Markov + EMA)",    "available": True},
        {"value": "kronos-mini",   "label": "Kronos mini (~4M params)",           "available": kronos_ok},
        {"value": "kronos-small",  "label": "Kronos small (~25M params)",         "available": kronos_ok},
        {"value": "kronos-base",   "label": "Kronos base (~100M params)",         "available": kronos_ok},
        {"value": "ensemble",      "label": "Both side by side (ensemble)",       "available": True},
    ]

    return {
        "env_default": env_default,
        "override": override,
        "effective": effective,
        "select_value": select_value,
        "active": active,
        "kronos_available": kronos_ok,
        "kronos_size": size,
        "device": device,
        "reason": reason,
        "options": options,
    }


def _asset_version(*relpaths: str) -> str:
    """Cache-bust string for static assets.

    Returns the max mtime (as int seconds) across the given files so the
    browser refetches whenever any of them change. Falls back to the
    process start time if a file is missing.
    """
    static_root = ROOT / "dashboard" / "static"
    latest = 0.0
    for rel in relpaths:
        p = static_root / rel
        try:
            latest = max(latest, p.stat().st_mtime)
        except OSError:
            continue
    if latest == 0.0:
        latest = time.time()
    return str(int(latest))


def _page_context() -> Dict[str, Any]:
    return {
        "model": settings.openrouter_model,
        "assets": settings.assets,
        "bankroll": settings.bankroll_usd,
        "max_kelly": settings.max_kelly_fraction,
        "min_edge": settings.min_edge,
        "llm_on": settings.has_llm,
        "apify_on": settings.has_apify,
        "asset_v": _asset_version("styles.css", "app.js", "backtest.js"),
        "predictor": _predictor_status(),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", _page_context())


@app.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "backtest.html",
        {**_page_context(), "predictors": list_available_predictors()},
    )


# --------------------------------------------------------------------------- #
# Live-desk endpoints                                                         #
# --------------------------------------------------------------------------- #


@app.get("/api/state")
def state() -> JSONResponse:
    cycles = load_recent_cycles(20)
    fb = load_recent_feedback(200)
    return JSONResponse(
        {
            "stats": {
                "n_feedback": len(fb),
                "hit_rate": hit_rate(fb),
                "total_pnl_usd": total_pnl(fb),
            },
            "settings": {
                "model": settings.openrouter_model,
                "assets": settings.assets,
                "bankroll_usd": settings.bankroll_usd,
                "max_kelly": settings.max_kelly_fraction,
                "min_edge": settings.min_edge,
                "llm_on": settings.has_llm,
                "apify_on": settings.has_apify,
            },
            "predictor": _predictor_status(),
            "latest_cycle": cycles[-1] if cycles else None,
            "audit_tail": tail_audit(80),
        }
    )


class PredictorChoice(BaseModel):
    value: str = ""


@app.get("/api/predictor")
def get_predictor() -> JSONResponse:
    """Current predictor status + the menu of choices the dashboard renders."""
    return JSONResponse(_predictor_status())


@app.post("/api/predictor")
def set_predictor(choice: PredictorChoice) -> JSONResponse:
    """Switch the predictor at runtime (no restart needed).

    Body: {"value": "auto" | "statistical" | "kronos-mini" | "kronos-small"
                  | "kronos-base" | "ensemble" | "" (clear)}
    The override is in-memory only -- it lasts until the dashboard
    process is restarted, at which point the env-default takes over.
    """
    try:
        cleaned = set_runtime_override(choice.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("[api]predictor override set to %r", cleaned or "(cleared)")
    return JSONResponse(_predictor_status())


@app.get("/api/run")
def run_cycle() -> JSONResponse:
    cycle = get_orch().run_once()
    return JSONResponse(cycle.model_dump(mode="json"))


@app.get("/api/predictors")
def predictors() -> JSONResponse:
    return JSONResponse({"predictors": list_available_predictors()})


# --------------------------------------------------------------------------- #
# Backtest endpoints                                                          #
# --------------------------------------------------------------------------- #


class BacktestRequest(BaseModel):
    asset: str = "BTC"
    interval: str = "5m"
    horizon_minutes: int = 5
    days: int = 14
    warmup_bars: int = 200
    step_bars: int = 1
    models: List[str] = Field(default_factory=lambda: ["statistical"])
    bankroll_usd: Optional[float] = None
    max_kelly_fraction: Optional[float] = None
    min_edge: Optional[float] = None
    market_price_anchor: float = 0.5
    market_price_noise: float = 0.04
    seed: int = 42
    source: str = "auto"
    label: str = ""


_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _set_job(job_id: str, **fields: Any) -> None:
    with _JOBS_LOCK:
        job = _JOBS.setdefault(job_id, {"id": job_id, "created_at": time.time()})
        job.update(fields)
        job["updated_at"] = time.time()


def _get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def _run_in_background(job_id: str, spec: BacktestSpec) -> None:
    def progress(pct: float, msg: str) -> None:
        _set_job(job_id, progress=round(pct, 4), message=msg, status="running")

    _set_job(job_id, status="running", progress=0.0, message="starting...", spec=spec.__dict__)
    try:
        result = run_backtest(spec, progress=progress)
        payload = result_to_payload(result)
        save_backtest(payload)
        _set_job(
            job_id,
            status="done",
            progress=1.0,
            message="done",
            result_id=result.id,
            summary_per_model=payload["summary_per_model"],
            pairwise=payload["pairwise"],
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("backtest job %s failed", job_id)
        _set_job(job_id, status="error", message=str(exc))


@app.post("/api/backtest/run")
def api_backtest_run(req: BacktestRequest) -> JSONResponse:
    spec = BacktestSpec(
        asset=req.asset.upper(),
        interval=req.interval,
        horizon_minutes=req.horizon_minutes,
        days=req.days,
        warmup_bars=req.warmup_bars,
        step_bars=max(1, int(req.step_bars)),
        models=[m.strip() for m in req.models if m.strip()] or ["statistical"],
        bankroll_usd=float(req.bankroll_usd) if req.bankroll_usd is not None else settings.bankroll_usd,
        max_kelly_fraction=(
            float(req.max_kelly_fraction)
            if req.max_kelly_fraction is not None
            else settings.max_kelly_fraction
        ),
        min_edge=float(req.min_edge) if req.min_edge is not None else settings.min_edge,
        market_price_anchor=float(req.market_price_anchor),
        market_price_noise=float(req.market_price_noise),
        seed=int(req.seed),
        source=req.source,
        label=req.label,
    )

    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, status="queued", progress=0.0, message="queued")
    threading.Thread(
        target=_run_in_background,
        args=(job_id, spec),
        daemon=True,
        name=f"backtest-{job_id}",
    ).start()

    return JSONResponse({"job_id": job_id, "status": "queued"})


@app.get("/api/backtest/jobs/{job_id}")
def api_backtest_job(job_id: str) -> JSONResponse:
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JSONResponse(_sanitize(job))


@app.get("/api/backtest/list")
def api_backtest_list() -> JSONResponse:
    return JSONResponse({"runs": list_backtests()})


@app.get("/api/backtest/{run_id}")
def api_backtest_get(run_id: str) -> JSONResponse:
    payload = load_backtest(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse(payload)


@app.delete("/api/backtest/{run_id}")
def api_backtest_delete(run_id: str) -> JSONResponse:
    ok = delete_backtest(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse({"deleted": run_id})
