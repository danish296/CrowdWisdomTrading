"""
Crypto OHLC fetcher.

Order of preference:
  1. Apify (if APIFY_TOKEN set) — runs a configurable actor that returns
     bars. We accept either an Apify dataset of {ts, open, high, low,
     close, volume} dicts or a list of Binance-style klines.
  2. Binance public REST `/api/v3/klines` (no key required) — the most
     reliable free source of crypto OHLC.
  3. Synthetic random-walk DEMO bars so the pipeline never stalls.
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from config import settings
from core.logger import audit, get_logger
from core.types import OHLCBar, PriceSeries

log = get_logger("tool.data")

BINANCE = "https://api.binance.com/api/v3/klines"

_INTERVAL_TO_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}


def _binance_symbol(asset: str) -> str:
    return asset.upper() + "USDT"


def fetch_bars(
    asset: str,
    interval: str = "1m",
    limit: int = 1000,
) -> PriceSeries:
    """Fetch OHLCV bars for `asset`. Tries Apify -> Binance -> synthetic."""
    if settings.has_apify:
        try:
            bars = _fetch_apify(asset, interval, limit)
            if bars:
                return PriceSeries(
                    asset=asset, interval=interval, bars=bars, source="apify"
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Apify fetch failed (%s) — falling back to Binance", exc)
            audit("apify_error", error=str(exc), asset=asset)

    try:
        bars = _fetch_binance(asset, interval, limit)
        if bars:
            return PriceSeries(
                asset=asset, interval=interval, bars=bars, source="binance"
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Binance fetch failed (%s) — using synthetic bars", exc)
        audit("binance_error", error=str(exc), asset=asset)

    return PriceSeries(
        asset=asset,
        interval=interval,
        bars=_synth_bars(asset, interval, limit),
        source="demo",
    )


# --------------------------------------------------------------------------- #
# Apify                                                                       #
# --------------------------------------------------------------------------- #


def _fetch_apify(asset: str, interval: str, limit: int) -> List[OHLCBar]:
    from apify_client import ApifyClient  # imported lazily

    client = ApifyClient(settings.apify_token)
    actor_id = settings.apify_polymarket_actor  # reused as generic scraper

    # We pass a generic Binance URL; the actor we point at must scrape it.
    # Users can swap APIFY_POLYMARKET_ACTOR for a custom crypto-OHLC actor.
    run_input = {
        "startUrls": [
            {
                "url": (
                    f"{BINANCE}?symbol={_binance_symbol(asset)}"
                    f"&interval={interval}&limit={limit}"
                )
            }
        ],
        "maxRequestsPerCrawl": 1,
    }
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=60)
    if not run:
        return []
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return _items_to_bars(items)


def _items_to_bars(items: list) -> List[OHLCBar]:
    bars: List[OHLCBar] = []
    for it in items:
        # Already-shaped dict?
        if isinstance(it, dict) and {"open", "high", "low", "close"}.issubset(it):
            ts = it.get("ts") or it.get("openTime") or it.get("time") or time.time()
            bars.append(_mk_bar(ts, it["open"], it["high"], it["low"], it["close"], it.get("volume", 0)))
            continue
        # Binance-style array?
        if isinstance(it, list) and len(it) >= 6:
            bars.append(_mk_bar(it[0], it[1], it[2], it[3], it[4], it[5]))
    return bars


# --------------------------------------------------------------------------- #
# Binance                                                                     #
# --------------------------------------------------------------------------- #


def _fetch_binance(asset: str, interval: str, limit: int) -> List[OHLCBar]:
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            BINANCE,
            params={
                "symbol": _binance_symbol(asset),
                "interval": interval,
                "limit": min(limit, 1000),
            },
        )
    resp.raise_for_status()
    return [_mk_bar(r[0], r[1], r[2], r[3], r[4], r[5]) for r in resp.json()]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _mk_bar(ts, o, h, l, c, v) -> OHLCBar:
    if isinstance(ts, (int, float)) and ts > 1e11:
        dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
    elif isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    return OHLCBar(
        ts=dt,
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        volume=float(v) if v is not None else 0.0,
    )


def _synth_bars(asset: str, interval: str, limit: int) -> List[OHLCBar]:
    step_ms = _INTERVAL_TO_MS.get(interval, 60_000)
    base = {"BTC": 65_000.0, "ETH": 3_200.0, "SOL": 160.0}.get(asset.upper(), 100.0)
    now = datetime.now(timezone.utc)
    bars: List[OHLCBar] = []
    price = base
    rnd = random.Random(hash((asset, int(time.time() / 600))))
    for i in range(limit):
        drift = rnd.gauss(0, base * 0.0008)
        new_price = max(0.01, price + drift + math.sin(i / 9) * base * 0.0003)
        h = max(price, new_price) * (1 + abs(rnd.gauss(0, 0.0003)))
        l = min(price, new_price) * (1 - abs(rnd.gauss(0, 0.0003)))
        bars.append(
            OHLCBar(
                ts=now - timedelta(milliseconds=step_ms * (limit - i)),
                open=price,
                high=h,
                low=l,
                close=new_price,
                volume=abs(rnd.gauss(50, 20)),
            )
        )
        price = new_price
    return bars
