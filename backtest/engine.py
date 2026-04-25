"""
Walk-forward backtester.

Protocol
--------
1. Pull `days` of contiguous, real OHLC bars from a public exchange.
2. For every anchor index i in [warmup .. len(bars) - horizon_bars - 1]
   step `step_bars`:
     a. For each model in `spec.models`, predict using bars[i-warmup+1 .. i]
        (a strict trailing window -- no look-ahead).
     b. Read the realised forward direction over the next horizon_bars.
     c. Construct a synthetic binary contract priced at `market_price_fn`
        of the last close (default: noisy 0.50 anchor) and apply the
        same Kelly + edge filter the live desk uses to compute the trade.
     d. Persist a Sample for every (model, anchor) pair.
3. Compute per-model metrics + Diebold-Mariano A vs B significance.

Why a synthetic contract price
------------------------------
Polymarket and Kalshi do not expose tick-level historical books on their
public APIs. To compare strategies fairly we need a counterfactual price
the strategy would have faced at every historical anchor. We use the
crowd-anchored model: every contract is priced at 0.50 plus a small
Gaussian noise term, which is what these short-horizon BTC/ETH markets
empirically trade around. Pass `market_price_fn=lambda anchor: 0.5` for
a strict crowd-anchor, or any other callable for a custom assumption.
This is a backtest assumption -- it is documented in the result so
nobody mistakes the simulated P&L for a live trading record.
"""
from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from core.logger import audit, get_logger
from core.types import PriceSeries
from tools import kronos_predictor

from .data import (
    INTERVAL_TO_SECONDS,
    FetchSpec,
    fetch_history,
    realised_direction,
    realised_return,
    slice_window,
)
from .metrics import (
    Sample,
    diebold_mariano,
    summarise,
)

log = get_logger("backtest.engine")


@dataclass
class BacktestSpec:
    asset: str = "BTC"
    interval: str = "5m"          # bar size used for prediction inputs
    horizon_minutes: int = 5      # forecast horizon
    days: int = 14                # how much history to pull
    warmup_bars: int = 200        # bars used as input window per prediction
    step_bars: int = 1            # spacing between successive anchors
    models: List[str] = field(    # default_factory below
        default_factory=lambda: ["statistical"]
    )
    bankroll_usd: float = 1000.0
    max_kelly_fraction: float = 0.25
    min_edge: float = 0.02
    market_price_anchor: float = 0.5
    market_price_noise: float = 0.04   # one-sigma noise around the anchor
    seed: int = 42
    source: str = "auto"          # auto | bitstamp | binance
    label: str = ""


@dataclass
class BacktestResult:
    id: str
    spec: BacktestSpec
    started_at: float
    finished_at: float
    n_bars: int
    bar_seconds: int
    samples_per_model: Dict[str, List[Sample]]
    summary_per_model: Dict[str, Dict]
    pairwise: List[Dict]
    notes: List[str]


# --------------------------------------------------------------------------- #
# Public                                                                      #
# --------------------------------------------------------------------------- #


def run_backtest(
    spec: BacktestSpec,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    market_price_fn: Optional[Callable[[int], float]] = None,
) -> BacktestResult:
    """Execute a walk-forward backtest. `progress(pct, message)` is
    called periodically so the dashboard can render a live bar."""
    rng = random.Random(spec.seed)
    started_at = time.time()
    notes: List[str] = []

    # 1. Pull real history.
    if progress:
        progress(0.02, f"Fetching {spec.days}d of {spec.interval} {spec.asset} bars...")

    series = fetch_history(
        FetchSpec(
            asset=spec.asset,
            interval=spec.interval,
            days=spec.days,
            source=spec.source,
        )
    )
    n_bars = len(series.bars)
    bar_seconds = INTERVAL_TO_SECONDS[spec.interval]
    horizon_bars = max(1, spec.horizon_minutes // (bar_seconds // 60 or 1))

    notes.append(
        f"data_source={series.source} bars={n_bars} interval={spec.interval} "
        f"horizon_bars={horizon_bars}"
    )
    log.info("backtest.engine: %s", notes[-1])

    if n_bars < spec.warmup_bars + horizon_bars + 10:
        raise RuntimeError(
            f"Not enough bars ({n_bars}) for warmup={spec.warmup_bars} + "
            f"horizon={horizon_bars}. Pull more days or shorten warmup."
        )

    # 2. Walk forward.
    samples_per_model: Dict[str, List[Sample]] = {m: [] for m in spec.models}
    last = n_bars - horizon_bars - 1
    anchors = list(range(spec.warmup_bars - 1, last, spec.step_bars))
    total_anchors = len(anchors)
    if total_anchors == 0:
        raise RuntimeError("No anchors produced -- check step_bars and warmup_bars.")

    failures: Dict[str, int] = {m: 0 for m in spec.models}
    first_failure_logged: Dict[str, bool] = {m: False for m in spec.models}
    last_progress = 0.0

    for idx, anchor in enumerate(anchors):
        sub = slice_window(series, anchor, spec.warmup_bars)
        realised_dir = realised_direction(series, anchor, horizon_bars)
        ret = realised_return(series, anchor, horizon_bars)
        if realised_dir is None or ret is None:
            continue
        realised_up = 1 if realised_dir == "UP" else 0

        # Synthetic counterfactual market price
        if market_price_fn is not None:
            mp = float(market_price_fn(anchor))
        else:
            mp = spec.market_price_anchor + rng.gauss(0.0, spec.market_price_noise)
        mp = max(0.05, min(0.95, mp))

        anchor_ts = sub.bars[-1].ts.replace(tzinfo=timezone.utc).timestamp()

        for model_name in spec.models:
            try:
                p = kronos_predictor.predict_next_move(
                    sub, horizon_minutes=spec.horizon_minutes, model=model_name,
                )
            except Exception as exc:  # noqa: BLE001
                failures[model_name] += 1
                if not first_failure_logged[model_name]:
                    log.warning(
                        "backtest.engine: %s failing -- first error: %s "
                        "(further errors for this model will be counted "
                        "silently and reported in the summary)",
                        model_name, exc,
                    )
                    first_failure_logged[model_name] = True
                continue

            sample = _trade(
                model_prob_up=float(p.prob_up),
                model_confidence=float(p.confidence),
                market_price=mp,
                realised_up=realised_up,
                realised_return=float(ret),
                spec=spec,
                anchor_ts=anchor_ts,
                horizon_minutes=spec.horizon_minutes,
                asset=spec.asset.upper(),
            )
            samples_per_model[model_name].append(sample)

        # Progress (throttled to 1% steps)
        pct = idx / total_anchors
        if progress and pct - last_progress >= 0.01:
            progress(
                0.05 + 0.9 * pct,
                f"walk-forward {idx + 1}/{total_anchors} anchors",
            )
            last_progress = pct

    # 3. Per-model summary.
    if progress:
        progress(0.96, "computing metrics...")

    for model_name, count in failures.items():
        if count > 0:
            notes.append(
                f"{model_name}: {count}/{total_anchors} anchors failed "
                f"(check install -- not silently substituted with another model)"
            )

    periods_per_year = max(1, int(365 * 24 * 3600 / bar_seconds))
    summary_per_model: Dict[str, Dict] = {}
    for model_name, samples in samples_per_model.items():
        s = summarise(samples, spec.bankroll_usd, periods_per_year)
        s["failures"] = failures.get(model_name, 0)
        summary_per_model[model_name] = s

    # 4. Pairwise Diebold-Mariano on Brier-style squared losses.
    pairwise: List[Dict] = []
    keys = list(samples_per_model.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            sa = samples_per_model[a]
            sb = samples_per_model[b]
            n = min(len(sa), len(sb))
            if n < 30:
                continue
            la = [(s.prob_up - s.realised_up) ** 2 for s in sa[:n]]
            lb = [(s.prob_up - s.realised_up) ** 2 for s in sb[:n]]
            test = diebold_mariano(la, lb)
            test["a"] = a
            test["b"] = b
            test["interpretation"] = _interpret_dm(test, a, b)
            pairwise.append(test)

    finished_at = time.time()
    if progress:
        progress(1.0, "done")

    bid = uuid.uuid4().hex[:12]
    audit(
        "backtest_done",
        id=bid,
        asset=spec.asset,
        days=spec.days,
        models=spec.models,
        n_bars=n_bars,
        anchors=total_anchors,
        elapsed_s=round(finished_at - started_at, 1),
    )
    log.info(
        "backtest.engine: completed %s in %.1fs (%d anchors x %d models)",
        bid, finished_at - started_at, total_anchors, len(spec.models),
    )

    return BacktestResult(
        id=bid,
        spec=spec,
        started_at=started_at,
        finished_at=finished_at,
        n_bars=n_bars,
        bar_seconds=bar_seconds,
        samples_per_model=samples_per_model,
        summary_per_model=summary_per_model,
        pairwise=pairwise,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Trade simulation (Kelly + edge filter, mirrors the live desk)               #
# --------------------------------------------------------------------------- #


def _trade(
    *,
    model_prob_up: float,
    model_confidence: float,
    market_price: float,
    realised_up: int,
    realised_return: float,
    spec: BacktestSpec,
    anchor_ts: float,
    horizon_minutes: int,
    asset: str,
) -> Sample:
    yes_edge = model_prob_up - market_price
    no_edge = (1.0 - model_prob_up) - (1.0 - market_price)

    side = "SKIP"
    edge_used = 0.0
    true_p = 0.5
    price = market_price
    stake = 0.0

    if yes_edge >= no_edge and yes_edge > 0:
        side = "YES"
        edge_used = yes_edge
        true_p = model_prob_up
        price = market_price
    elif no_edge > 0:
        side = "NO"
        edge_used = no_edge
        true_p = 1.0 - model_prob_up
        price = 1.0 - market_price

    if edge_used >= spec.min_edge and price > 0 and price < 1:
        b = (1 - price) / price
        kelly = max(0.0, (b * true_p - (1 - true_p)) / b)
        kelly *= max(0.0, min(1.0, model_confidence))
        kelly = min(kelly, spec.max_kelly_fraction)
        stake = round(kelly * spec.bankroll_usd, 2)

    pnl = 0.0
    if side == "YES" and stake > 0:
        won = realised_up == 1
        pnl = stake * ((1 - market_price) / market_price) if won else -stake
    elif side == "NO" and stake > 0:
        won = realised_up == 0
        no_price = 1.0 - market_price
        pnl = stake * ((1 - no_price) / no_price) if won else -stake

    if stake == 0:
        side = "SKIP"

    return Sample(
        ts_unix=float(anchor_ts),
        asset=asset,
        horizon_minutes=horizon_minutes,
        prob_up=float(model_prob_up),
        realised_up=int(realised_up),
        realised_return=float(realised_return),
        confidence=float(model_confidence),
        market_price=float(market_price),
        side_taken=side,
        stake=float(stake),
        pnl=float(round(pnl, 4)),
    )


def _interpret_dm(test: Dict, a: str, b: str) -> str:
    p = test.get("p_value", float("nan"))
    dm = test.get("dm_stat", float("nan"))
    if p != p:
        return "insufficient samples"
    if p > 0.10:
        return f"{a} vs {b}: difference not significant (p={p:.3f})"
    winner = a if dm < 0 else b
    loser = b if dm < 0 else a
    sig = "marginal" if p > 0.05 else "significant"
    return f"{winner} beats {loser} on Brier loss ({sig}, p={p:.3f})"


# --------------------------------------------------------------------------- #
# Serialisation helpers used by the API/store                                 #
# --------------------------------------------------------------------------- #


def result_to_payload(res: BacktestResult) -> Dict:
    """Convert a BacktestResult into a JSON-serialisable dict for storage
    and the dashboard."""
    return {
        "id": res.id,
        "spec": res.spec.__dict__,
        "started_at": res.started_at,
        "finished_at": res.finished_at,
        "elapsed_s": round(res.finished_at - res.started_at, 2),
        "n_bars": res.n_bars,
        "bar_seconds": res.bar_seconds,
        "started_at_iso": datetime.fromtimestamp(res.started_at, tz=timezone.utc).isoformat(),
        "finished_at_iso": datetime.fromtimestamp(res.finished_at, tz=timezone.utc).isoformat(),
        "summary_per_model": res.summary_per_model,
        "pairwise": res.pairwise,
        "notes": res.notes,
        "samples_per_model": {
            m: [s.__dict__ for s in sm]
            for m, sm in res.samples_per_model.items()
        },
        "equity_per_model": {
            m: _equity(sm, res.spec.bankroll_usd)
            for m, sm in res.samples_per_model.items()
        },
    }


def _equity(samples: Sequence[Sample], bankroll: float) -> List[Dict[str, float]]:
    bal = bankroll
    out = [{"ts": float(samples[0].ts_unix) if samples else 0.0, "equity": bal}] if samples else []
    for s in samples:
        bal += s.pnl
        out.append({"ts": float(s.ts_unix), "equity": float(round(bal, 4))})
    return out
