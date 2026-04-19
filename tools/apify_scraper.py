"""
Crypto OHLC fetcher.

Order of preference:
  1. Apify (if APIFY_TOKEN set) — runs a configurable Apify actor that
     scrapes a public OHLC endpoint and returns bars. We default to
     Bitstamp's public OHLC endpoint because Apify Proxy IPs are
     globally allowed there (Binance returns HTTP 451 to Apify Proxy).
  2. Binance public REST `/api/v3/klines` (no key required) — the most
     reliable free source of OHLC when called from your own IP.
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
BITSTAMP = "https://www.bitstamp.net/api/v2/ohlc"

_INTERVAL_TO_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
}

_INTERVAL_TO_BITSTAMP_STEP = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}


def _binance_symbol(asset: str) -> str:
    return asset.upper() + "USDT"


def _bitstamp_pair(asset: str) -> str:
    """Map BTC -> btcusd, ETH -> ethusd, etc. Bitstamp uses lowercase pairs."""
    return f"{asset.lower()}usd"


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


# pageFunction for Cheerio/Web/Puppeteer scrapers. The Cheerio scraper
# auto-parses application/json responses into context.json, so we read
# that first and fall back to body parsing for HTML/Puppeteer engines.
# Bitstamp's response shape is:
#   { "data": { "pair": "BTC/USD", "ohlc": [
#       {"timestamp":"...","open":"...","high":"...",
#        "low":"...","close":"...","volume":"..."}, ...
#   ]}}
_PAGE_FUNCTION = """
async function pageFunction(context) {
    const { request, log, body, json } = context;
    let parsed = json;
    if (!parsed) {
        let raw = body;
        if (!raw && typeof document !== 'undefined') {
            raw = document.body ? document.body.innerText : '';
        }
        try { parsed = JSON.parse(raw); }
        catch (e) { log.error('Apify pageFunction: JSON parse failed: ' + e.message); }
    }
    const ohlc = (parsed && parsed.data && Array.isArray(parsed.data.ohlc))
        ? parsed.data.ohlc : [];
    return { url: request.url, pair: parsed && parsed.data && parsed.data.pair, ohlc: ohlc };
}
""".strip()


def _fetch_apify(asset: str, interval: str, limit: int) -> List[OHLCBar]:
    from apify_client import ApifyClient  # imported lazily

    client = ApifyClient(settings.apify_token)
    actor_id = settings.apify_polymarket_actor  # reused as generic scraper

    # We target Bitstamp because its OHLC endpoint allows Apify Proxy
    # IPs (Binance returns HTTP 451 to them). One request returns up to
    # 1000 bars, matching the project default.
    step = _INTERVAL_TO_BITSTAMP_STEP.get(interval, 60)
    capped_limit = min(limit, 1000)
    target_url = (
        f"{BITSTAMP}/{_bitstamp_pair(asset)}/?step={step}&limit={capped_limit}"
    )
    run_input = {
        "startUrls": [{"url": target_url}],
        "pageFunction": _PAGE_FUNCTION,
        "maxRequestsPerCrawl": 1,
        "maxRequestRetries": 1,
        "proxyConfiguration": {"useApifyProxy": True},
        "additionalMimeTypes": ["application/json", "text/plain"],
    }
    log.info("Apify: dispatching %s -> %s", actor_id, target_url)
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=180)
    if not run:
        return []
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    bars = _items_to_bars(items)
    if bars:
        audit("apify_ok", actor=actor_id, asset=asset, bars=len(bars), run_id=run.get("id"))
    return bars


def _items_to_bars(items: list) -> List[OHLCBar]:
    bars: List[OHLCBar] = []
    for it in items:
        # Bitstamp shape from our pageFunction: {"ohlc": [{...}, ...]}
        if isinstance(it, dict) and isinstance(it.get("ohlc"), list):
            for r in it["ohlc"]:
                if isinstance(r, dict) and {"open", "high", "low", "close"}.issubset(r):
                    bars.append(
                        _mk_bar(
                            r.get("timestamp"),
                            r["open"], r["high"], r["low"], r["close"],
                            r.get("volume", 0),
                        )
                    )
            continue
        # Generic array-of-klines shape (Binance-like)
        if isinstance(it, dict) and isinstance(it.get("klines"), list):
            for k in it["klines"]:
                if isinstance(k, list) and len(k) >= 6:
                    bars.append(_mk_bar(k[0], k[1], k[2], k[3], k[4], k[5]))
            continue
        # Already-shaped OHLC dict
        if isinstance(it, dict) and {"open", "high", "low", "close"}.issubset(it):
            ts = it.get("ts") or it.get("timestamp") or it.get("openTime") or it.get("time") or time.time()
            bars.append(_mk_bar(ts, it["open"], it["high"], it["low"], it["close"], it.get("volume", 0)))
            continue
        # Raw Binance-style kline array
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
    raw = ts
    # Coerce numeric strings (e.g. Bitstamp "1761934800") into floats first.
    if isinstance(raw, str):
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            pass
    if isinstance(raw, (int, float)) and raw > 1e11:
        dt = datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
    elif isinstance(raw, (int, float)):
        dt = datetime.fromtimestamp(raw, tz=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
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
