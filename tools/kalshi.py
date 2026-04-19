"""
Kalshi adapter. Kalshi exposes a public read-only events endpoint that
does not require auth for browsing markets:

    https://api.elections.kalshi.com/trade-api/v2/markets

We pull crypto-tagged markets that close within the requested horizon
window. As with Polymarket, we degrade gracefully to a DEMO market if the
API is unreachable.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from core.logger import audit, get_logger
from core.types import Market

log = get_logger("tool.kalshi")

KALSHI_MARKETS = "https://api.elections.kalshi.com/trade-api/v2/markets"


def _synth(asset: str, horizon: int = 5) -> Market:
    yes = round(random.uniform(0.40, 0.60), 3)
    return Market(
        venue="kalshi",
        market_id=f"demo-kalshi-{asset.lower()}-{int(time.time())}",
        question=f"Will {asset} close higher in {horizon} min? (DEMO)",
        asset=asset,
        horizon_minutes=horizon,
        yes_price=yes,
        no_price=round(1 - yes, 3),
        volume_usd=random.uniform(1_000, 25_000),
        url="https://kalshi.com/",
    )


def search_markets(
    assets: List[str],
    horizon_minutes: int = 5,
    limit_per_asset: int = 3,
) -> List[Market]:
    out: List[Market] = []
    for asset in assets:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(
                    KALSHI_MARKETS,
                    params={"limit": 100, "status": "open"},
                )
            resp.raise_for_status()
            picks = _filter_markets(resp.json(), asset, horizon_minutes)
            picks = picks[:limit_per_asset]
            if picks:
                out.extend(picks)
                continue
            log.warning("Kalshi: no live %s markets, using DEMO", asset)
            out.append(_synth(asset, horizon_minutes))
        except Exception as exc:  # noqa: BLE001
            log.warning("Kalshi fetch failed (%s) — DEMO fallback", exc)
            audit("kalshi_error", error=str(exc), asset=asset)
            out.append(_synth(asset, horizon_minutes))
    return out


def _filter_markets(payload: dict, asset: str, horizon: int) -> List[Market]:
    out: List[Market] = []
    keyword = asset.upper()
    now = datetime.now(timezone.utc)
    rows = payload.get("markets") or []
    for r in rows:
        title = (r.get("title") or r.get("subtitle") or "").upper()
        if keyword not in title:
            continue
        close_iso = r.get("close_time") or ""
        try:
            close = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        delta = close - now
        if not (
            timedelta(minutes=1) < delta < timedelta(minutes=max(horizon * 4, 60))
        ):
            continue
        # Kalshi prices are in cents (0..100)
        yes_cents = r.get("yes_bid") or r.get("last_price") or 50
        try:
            yes = float(yes_cents) / 100.0
        except (TypeError, ValueError):
            yes = 0.5
        out.append(
            Market(
                venue="kalshi",
                market_id=str(r.get("ticker") or r.get("id") or "unknown"),
                question=r.get("title", "")[:240],
                asset=asset.upper(),
                horizon_minutes=horizon,
                yes_price=max(0.01, min(0.99, yes)),
                no_price=max(0.01, min(0.99, 1 - yes)),
                volume_usd=float(r.get("volume") or 0.0),
                url=f"https://kalshi.com/markets/{r.get('event_ticker','')}",
            )
        )
    return out
