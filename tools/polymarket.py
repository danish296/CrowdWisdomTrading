"""
Polymarket adapter. Uses the public gamma-api (no auth required) to find
short-horizon binary markets on BTC/ETH "will price be up in N minutes?".

If the network call fails (firewall, region block, rate limit) we fall
back to a synthetic but realistic market so the rest of the pipeline can
keep running — the orchestrator never crashes because of an external
service.
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import List

import httpx

from core.logger import audit, get_logger
from core.types import Market

log = get_logger("tool.polymarket")

GAMMA = "https://gamma-api.polymarket.com/markets"


def _synth(asset: str, horizon: int = 5) -> Market:
    """Realistic placeholder when the API is unreachable."""
    yes = round(random.uniform(0.42, 0.58), 3)
    return Market(
        venue="polymarket",
        market_id=f"demo-poly-{asset.lower()}-{int(time.time())}",
        question=f"Will {asset}/USD be higher in {horizon} minutes? (DEMO)",
        asset=asset,
        horizon_minutes=horizon,
        yes_price=yes,
        no_price=round(1 - yes, 3),
        volume_usd=random.uniform(2_500, 50_000),
        url="https://polymarket.com/",
    )


def search_markets(
    assets: List[str],
    horizon_minutes: int = 5,
    limit_per_asset: int = 3,
) -> List[Market]:
    """Search Polymarket for active short-horizon crypto markets."""
    out: List[Market] = []
    for asset in assets:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(
                    GAMMA,
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": 50,
                        "tag_id": 21,         # crypto tag
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            picks = _filter_markets(data, asset, horizon_minutes)[:limit_per_asset]
            if picks:
                out.extend(picks)
                continue
            log.warning("Polymarket: no live %s markets, using DEMO", asset)
            out.append(_synth(asset, horizon_minutes))
        except Exception as exc:  # noqa: BLE001
            log.warning("Polymarket fetch failed (%s) — DEMO fallback", exc)
            audit("polymarket_error", error=str(exc), asset=asset)
            out.append(_synth(asset, horizon_minutes))
    return out


def _filter_markets(rows: list, asset: str, horizon: int) -> List[Market]:
    out: List[Market] = []
    keyword = asset.upper()
    now = datetime.now(timezone.utc)
    for r in rows:
        q = (r.get("question") or "").upper()
        if keyword not in q:
            continue
        try:
            end = datetime.fromisoformat(
                (r.get("endDate") or "").replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue
        # Keep markets that end in the next ~horizon * 4 window
        if not (
            timedelta(minutes=1)
            < (end - now)
            < timedelta(minutes=max(horizon * 4, 60))
        ):
            continue
        try:
            yes = float((r.get("outcomePrices") or ["0.5", "0.5"])[0])
        except (TypeError, ValueError, IndexError):
            yes = 0.5
        out.append(
            Market(
                venue="polymarket",
                market_id=str(r.get("id") or r.get("conditionId") or "unknown"),
                question=r.get("question", "")[:240],
                asset=asset.upper(),
                horizon_minutes=horizon,
                yes_price=max(0.01, min(0.99, yes)),
                no_price=max(0.01, min(0.99, 1 - yes)),
                volume_usd=float(r.get("volume24hr") or 0.0),
                url=f"https://polymarket.com/event/{r.get('slug', '')}",
            )
        )
    return out
