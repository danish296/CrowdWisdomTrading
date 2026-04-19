"""
FastAPI dashboard.

Endpoints
---------
GET /                  HTML dashboard (Jinja template)
GET /api/state         JSON: latest cycle + feedback stats + audit tail
GET /api/run           Trigger a one-shot cycle (synchronous, ~5-15 s)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agents.orchestrator import Orchestrator
from config import settings
from core.logger import tail_audit
from core.memory import (
    hit_rate,
    load_recent_cycles,
    load_recent_feedback,
    total_pnl,
)

ROOT = Path(__file__).resolve().parent

app = FastAPI(title="CryptoAdsAgents", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(ROOT / "dashboard" / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "dashboard" / "templates"))

# Lazily-instantiated singleton so the cycle-history persists across requests.
_orch: Orchestrator | None = None


def get_orch() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "model": settings.openrouter_model,
            "assets": settings.assets,
            "bankroll": settings.bankroll_usd,
            "max_kelly": settings.max_kelly_fraction,
            "min_edge": settings.min_edge,
            "llm_on": settings.has_llm,
            "apify_on": settings.has_apify,
        },
    )


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
            "latest_cycle": cycles[-1] if cycles else None,
            "audit_tail": tail_audit(80),
        }
    )


@app.get("/api/run")
def run_cycle() -> JSONResponse:
    cycle = get_orch().run_once()
    return JSONResponse(cycle.model_dump(mode="json"))
