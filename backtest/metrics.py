"""
Metrics for the backtester.

Every metric is computed independently per model so the dashboard can
show them side by side. We use Diebold-Mariano with a Newey-West
correction to test whether the difference in squared probability error
between two models is statistically significant.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# Per-prediction record                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class Sample:
    ts_unix: float
    asset: str
    horizon_minutes: int
    prob_up: float            # in [0, 1]
    realised_up: int          # 1 if UP, 0 if DOWN
    realised_return: float    # signed return at horizon
    confidence: float = 0.0
    market_price: float = 0.5
    side_taken: str = "SKIP"  # YES / NO / SKIP
    stake: float = 0.0
    pnl: float = 0.0


# --------------------------------------------------------------------------- #
# Calibration                                                                 #
# --------------------------------------------------------------------------- #


def brier_score(samples: Sequence[Sample]) -> float:
    if not samples:
        return float("nan")
    arr = np.array(
        [(s.prob_up - s.realised_up) ** 2 for s in samples], dtype=np.float64
    )
    return float(np.mean(arr))


def log_loss(samples: Sequence[Sample], eps: float = 1e-9) -> float:
    if not samples:
        return float("nan")
    losses = []
    for s in samples:
        p = min(1 - eps, max(eps, s.prob_up))
        losses.append(-(s.realised_up * math.log(p) + (1 - s.realised_up) * math.log(1 - p)))
    return float(np.mean(losses))


def hit_rate(samples: Sequence[Sample], threshold: float = 0.5) -> float:
    """Fraction of times the directional call (prob_up > threshold) was right."""
    if not samples:
        return float("nan")
    correct = 0
    for s in samples:
        pred_up = s.prob_up > threshold
        if pred_up == bool(s.realised_up):
            correct += 1
    return correct / len(samples)


def confident_hit_rate(samples: Sequence[Sample], band: float = 0.05) -> Tuple[float, int]:
    """Hit rate restricted to predictions outside [0.5-band, 0.5+band]."""
    if not samples:
        return float("nan"), 0
    selected = [s for s in samples if abs(s.prob_up - 0.5) > band]
    if not selected:
        return float("nan"), 0
    return hit_rate(selected), len(selected)


def calibration_bins(
    samples: Sequence[Sample], n_bins: int = 10
) -> List[Dict[str, float]]:
    """Reliability diagram input: average predicted prob vs realised
    frequency of UP, per equal-width probability bin."""
    if not samples:
        return []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: List[Dict[str, float]] = []
    for i in range(n_bins):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i == n_bins - 1:
            bucket = [s for s in samples if lo <= s.prob_up <= hi]
        else:
            bucket = [s for s in samples if lo <= s.prob_up < hi]
        if not bucket:
            out.append({
                "bin_low": lo, "bin_high": hi, "n": 0,
                "avg_predicted": (lo + hi) / 2.0, "frequency_up": 0.0,
            })
            continue
        avg_pred = float(np.mean([s.prob_up for s in bucket]))
        freq_up = float(np.mean([s.realised_up for s in bucket]))
        out.append({
            "bin_low": lo,
            "bin_high": hi,
            "n": len(bucket),
            "avg_predicted": avg_pred,
            "frequency_up": freq_up,
        })
    return out


# --------------------------------------------------------------------------- #
# Trading P&L                                                                 #
# --------------------------------------------------------------------------- #


def equity_curve(samples: Sequence[Sample], starting_bankroll: float) -> List[Dict[str, float]]:
    """Cumulative P&L with timestamps, ready to plot in Chart.js."""
    bal = starting_bankroll
    curve: List[Dict[str, float]] = [
        {"ts": float(samples[0].ts_unix) if samples else 0.0, "equity": bal}
    ] if samples else []
    for s in samples:
        bal += s.pnl
        curve.append({"ts": float(s.ts_unix), "equity": float(round(bal, 4))})
    return curve


def total_pnl(samples: Sequence[Sample]) -> float:
    return float(sum(s.pnl for s in samples))


def n_trades(samples: Sequence[Sample]) -> int:
    return sum(1 for s in samples if s.side_taken in ("YES", "NO"))


def sharpe_ratio(samples: Sequence[Sample], periods_per_year: int = 365 * 24 * 12) -> float:
    """Annualised Sharpe over per-bar returns. periods_per_year defaults
    to a 5-minute bar (12 per hour, 24 hours, 365 days)."""
    rets = np.array([s.pnl for s in samples if s.side_taken in ("YES", "NO")], dtype=np.float64)
    if rets.size < 2:
        return float("nan")
    mu = float(np.mean(rets))
    sd = float(np.std(rets, ddof=1))
    if sd == 0.0:
        return float("nan")
    return float(mu / sd * math.sqrt(periods_per_year))


def max_drawdown(curve: Sequence[Dict[str, float]]) -> float:
    if not curve:
        return 0.0
    eq = np.array([c["equity"] for c in curve], dtype=np.float64)
    peaks = np.maximum.accumulate(eq)
    dd = (eq - peaks) / peaks
    return float(np.min(dd))


# --------------------------------------------------------------------------- #
# Diebold-Mariano significance                                                #
# --------------------------------------------------------------------------- #


def diebold_mariano(
    losses_a: Sequence[float],
    losses_b: Sequence[float],
    h: int = 1,
) -> Dict[str, float]:
    """Two-sided DM test on per-prediction loss series.

    Returns {dm_stat, p_value, n}. NaNs if either series is too short.
    """
    if len(losses_a) != len(losses_b) or len(losses_a) < 30:
        return {"dm_stat": float("nan"), "p_value": float("nan"), "n": float(len(losses_a))}

    d = np.array(losses_a, dtype=np.float64) - np.array(losses_b, dtype=np.float64)
    n = len(d)
    mean_d = float(np.mean(d))
    gamma_0 = float(np.var(d, ddof=0))
    var_d = gamma_0
    for k in range(1, h):
        cov = float(np.cov(d[:-k], d[k:], ddof=0)[0, 1])
        var_d += 2.0 * cov
    if var_d <= 0:
        return {"dm_stat": float("nan"), "p_value": float("nan"), "n": float(n)}

    dm = mean_d / math.sqrt(var_d / n)
    # Two-sided p value via standard-normal approx (Harvey et al. small-sample
    # correction is negligible at n > 200 which is our typical regime).
    p = 2.0 * (1.0 - _standard_normal_cdf(abs(dm)))
    return {"dm_stat": float(dm), "p_value": float(p), "n": float(n)}


def _standard_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# --------------------------------------------------------------------------- #
# Summary                                                                     #
# --------------------------------------------------------------------------- #


def summarise(
    samples: Sequence[Sample], starting_bankroll: float, periods_per_year: int
) -> Dict[str, float]:
    bins = calibration_bins(samples)
    eq = equity_curve(samples, starting_bankroll)
    conf_hit, conf_n = confident_hit_rate(samples, band=0.05)
    return {
        "n_predictions": len(samples),
        "n_trades": n_trades(samples),
        "hit_rate": hit_rate(samples),
        "confident_hit_rate": conf_hit,
        "confident_n": conf_n,
        "brier": brier_score(samples),
        "log_loss": log_loss(samples),
        "total_pnl_usd": round(total_pnl(samples), 2),
        "ending_equity_usd": round(eq[-1]["equity"], 2) if eq else starting_bankroll,
        "sharpe_annualised": sharpe_ratio(samples, periods_per_year=periods_per_year),
        "max_drawdown_pct": round(max_drawdown(eq) * 100.0, 3),
        "calibration_bins": bins,
    }
