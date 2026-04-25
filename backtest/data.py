"""
Real OHLC fetcher for the backtester.

Pulls a contiguous, time-ordered history of bars from public exchange
endpoints. We never substitute synthetic data here -- a backtest that
runs on fake bars is worse than no backtest, so any fetch error is
raised, not swallowed.

Source order
------------
1. Bitstamp public OHLC (/api/v2/ohlc) -- globally reachable, no key
   required, paginated to any depth. This is the primary source.
2. Binance public klines (/api/v3/klines) -- used only when Bitstamp
   does not list the asset (e.g. SOL/USD). Binance returns HTTP 451
   from many cloud regions; the caller decides whether to retry from
   another IP.

Bitstamp step values supported (seconds): 60, 180, 300, 900, 1800,
3600, 7200, 14400, 21600, 43200, 86400, 259200.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx

from core.logger import get_logger
from core.types import OHLCBar, PriceSeries

log = get_logger("backtest.data")

BITSTAMP = "https://www.bitstamp.net/api/v2/ohlc"
BINANCE = "https://api.binance.com/api/v3/klines"

INTERVAL_TO_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
}


def _bitstamp_pair(asset: str) -> str:
    return f"{asset.lower()}usd"


def _binance_symbol(asset: str) -> str:
    return f"{asset.upper()}USDT"


@dataclass
class FetchSpec:
    asset: str
    interval: str
    days: int = 30        # how much wall-clock history to fetch
    source: str = "auto"  # auto | bitstamp | binance


def fetch_history(spec: FetchSpec, *, http_timeout: float = 15.0) -> PriceSeries:
    if spec.interval not in INTERVAL_TO_SECONDS:
        raise ValueError(
            f"Unsupported interval {spec.interval!r}. Supported: "
            f"{sorted(INTERVAL_TO_SECONDS)}"
        )

    if spec.source in ("auto", "bitstamp"):
        try:
            bars = _fetch_bitstamp(spec, http_timeout=http_timeout)
            if bars:
                log.info(
                    "backtest.data: bitstamp returned %d bars for %s @ %s",
                    len(bars), spec.asset, spec.interval,
                )
                return PriceSeries(
                    asset=spec.asset.upper(),
                    interval=spec.interval,
                    bars=bars,
                    source="bitstamp",
                )
        except Exception as exc:  # noqa: BLE001
            if spec.source == "bitstamp":
                raise
            log.warning(
                "backtest.data: bitstamp fetch failed (%s) -- trying binance",
                exc,
            )

    if spec.source in ("auto", "binance"):
        bars = _fetch_binance(spec, http_timeout=http_timeout)
        if bars:
            log.info(
                "backtest.data: binance returned %d bars for %s @ %s",
                len(bars), spec.asset, spec.interval,
            )
            return PriceSeries(
                asset=spec.asset.upper(),
                interval=spec.interval,
                bars=bars,
                source="binance",
            )

    raise RuntimeError(
        f"No live data source could supply {spec.days}d of {spec.interval} "
        f"bars for {spec.asset}. Refusing to fabricate synthetic data for a "
        "backtest -- check your network or pick a different asset/interval."
    )


# --------------------------------------------------------------------------- #
# Bitstamp                                                                    #
# --------------------------------------------------------------------------- #


def _fetch_bitstamp(spec: FetchSpec, *, http_timeout: float) -> List[OHLCBar]:
    step = INTERVAL_TO_SECONDS[spec.interval]
    pair = _bitstamp_pair(spec.asset)

    end_ts = int(time.time())
    start_ts = end_ts - spec.days * 86400
    bars: List[OHLCBar] = []

    cursor_end = end_ts
    page_limit = 1000

    with httpx.Client(timeout=http_timeout) as client:
        while cursor_end > start_ts:
            cursor_start = max(start_ts, cursor_end - page_limit * step)
            params = {
                "step": step,
                "limit": page_limit,
                "start": cursor_start,
                "end": cursor_end,
                "exclude_current_candle": "true",
            }
            url = f"{BITSTAMP}/{pair}/"
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            ohlc = (payload.get("data") or {}).get("ohlc") or []
            if not ohlc:
                break

            page_bars = [_bitstamp_row_to_bar(r) for r in ohlc]
            bars.extend(page_bars)

            earliest = min(int(r["timestamp"]) for r in ohlc)
            new_cursor = earliest - step
            if new_cursor >= cursor_end:
                break  # safety -- we did not move backwards
            cursor_end = new_cursor
            time.sleep(0.05)  # be polite with bitstamp rate limits

    bars.sort(key=lambda b: b.ts)
    deduped = _dedup_bars(bars)
    return [b for b in deduped if b.ts.timestamp() >= start_ts]


def _bitstamp_row_to_bar(row: dict) -> OHLCBar:
    ts = int(row["timestamp"])
    return OHLCBar(
        ts=datetime.fromtimestamp(ts, tz=timezone.utc),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume") or 0.0),
    )


# --------------------------------------------------------------------------- #
# Binance                                                                     #
# --------------------------------------------------------------------------- #


def _fetch_binance(spec: FetchSpec, *, http_timeout: float) -> List[OHLCBar]:
    interval_ms = INTERVAL_TO_SECONDS[spec.interval] * 1000
    symbol = _binance_symbol(spec.asset)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - spec.days * 86400 * 1000
    bars: List[OHLCBar] = []

    cursor = start_ms
    page_limit = 1000

    with httpx.Client(timeout=http_timeout) as client:
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": spec.interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": page_limit,
            }
            resp = client.get(BINANCE, params=params)
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            for r in rows:
                bars.append(
                    OHLCBar(
                        ts=datetime.fromtimestamp(r[0] / 1000.0, tz=timezone.utc),
                        open=float(r[1]),
                        high=float(r[2]),
                        low=float(r[3]),
                        close=float(r[4]),
                        volume=float(r[5]),
                    )
                )
            last_open = int(rows[-1][0])
            new_cursor = last_open + interval_ms
            if new_cursor <= cursor:
                break
            cursor = new_cursor
            if len(rows) < page_limit:
                break
            time.sleep(0.05)

    bars.sort(key=lambda b: b.ts)
    return _dedup_bars(bars)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _dedup_bars(bars: List[OHLCBar]) -> List[OHLCBar]:
    seen: set = set()
    out: List[OHLCBar] = []
    for b in bars:
        key = b.ts
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def slice_window(series: PriceSeries, end_index: int, window: int) -> PriceSeries:
    """Return a PriceSeries of `window` bars ending at end_index (inclusive)."""
    start = max(0, end_index - window + 1)
    return PriceSeries(
        asset=series.asset,
        interval=series.interval,
        bars=list(series.bars[start : end_index + 1]),
        source=series.source,
    )


def realised_direction(
    series: PriceSeries, anchor_index: int, horizon_bars: int
) -> Optional[str]:
    """Look forward `horizon_bars` from anchor_index and return UP/DOWN/FLAT.

    Returns None if there is not enough future history.
    """
    target = anchor_index + horizon_bars
    if target >= len(series.bars):
        return None
    p0 = series.bars[anchor_index].close
    p1 = series.bars[target].close
    if p0 == 0:
        return None
    chg = (p1 - p0) / p0
    if chg > 0.0:
        return "UP"
    if chg < 0.0:
        return "DOWN"
    return "FLAT"


def realised_return(
    series: PriceSeries, anchor_index: int, horizon_bars: int
) -> Optional[float]:
    target = anchor_index + horizon_bars
    if target >= len(series.bars):
        return None
    p0 = series.bars[anchor_index].close
    p1 = series.bars[target].close
    if p0 == 0:
        return None
    return (p1 - p0) / p0
