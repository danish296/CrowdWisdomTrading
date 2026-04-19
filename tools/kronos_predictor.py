"""
Direction predictor.

Tries to import shiyu-coder/Kronos (a foundation model for K-line
forecasting). If the package or its weights aren't installed locally, we
fall back to a robust statistical predictor — an EMA-momentum + ATR-vol
estimator with Markov first-order state probabilities (inspired by the
"morning code" Markov-chain article cited in the project brief).

Both paths return the same `Prediction` shape so the orchestrator is
agnostic.
"""
from __future__ import annotations

import importlib
import math
from typing import List, Tuple

import numpy as np

from core.logger import get_logger
from core.types import OHLCBar, Prediction, PriceSeries

log = get_logger("tool.predict")


# --------------------------------------------------------------------------- #
# Public                                                                      #
# --------------------------------------------------------------------------- #


def predict_next_move(series: PriceSeries, horizon_minutes: int = 5) -> Prediction:
    """Return a direction + probability for the next `horizon_minutes`."""
    bars = series.bars
    if len(bars) < 30:
        return Prediction(
            asset=series.asset,
            horizon_minutes=horizon_minutes,
            direction="FLAT",
            prob_up=0.5,
            confidence=0.0,
            model_name="insufficient-data",
            rationale="Need ≥30 bars to predict.",
        )

    if _kronos_available():
        try:
            return _kronos_predict(series, horizon_minutes)
        except Exception as exc:  # noqa: BLE001
            log.warning("Kronos predictor failed (%s) — using statistical fallback", exc)

    return _statistical_predict(series, horizon_minutes)


# --------------------------------------------------------------------------- #
# Kronos (optional)                                                           #
# --------------------------------------------------------------------------- #


def _kronos_available() -> bool:
    try:
        importlib.import_module("torch")
        importlib.import_module("kronos")
        return True
    except ImportError:
        return False


def _kronos_predict(series: PriceSeries, horizon: int) -> Prediction:
    """
    Thin wrapper over Kronos. Implementation intentionally minimal — the
    real Kronos repo provides a `Kronos` class with a `predict` method.
    """
    import torch  # noqa: F401
    from kronos import Kronos  # type: ignore[import-not-found]

    closes = np.array([b.close for b in series.bars], dtype=np.float32)
    model = Kronos.from_pretrained("shiyu-coder/Kronos-base")
    pred = model.predict(closes, horizon=max(1, horizon))  # numpy of shape (h,)
    last = float(closes[-1])
    target = float(pred[-1])
    chg = (target - last) / last
    prob_up = float(1 / (1 + math.exp(-chg * 60)))  # squashed
    direction = "UP" if prob_up > 0.55 else "DOWN" if prob_up < 0.45 else "FLAT"
    return Prediction(
        asset=series.asset,
        horizon_minutes=horizon,
        direction=direction,  # type: ignore[arg-type]
        prob_up=round(prob_up, 4),
        confidence=min(1.0, abs(chg) * 100),
        model_name="kronos-base",
        rationale=f"Δ={chg:+.4%} over horizon",
    )


# --------------------------------------------------------------------------- #
# Statistical fallback                                                        #
# --------------------------------------------------------------------------- #


def _statistical_predict(series: PriceSeries, horizon: int) -> Prediction:
    bars: List[OHLCBar] = series.bars
    closes = np.array([b.close for b in bars], dtype=np.float64)
    rets = np.diff(closes) / closes[:-1]

    # EMA momentum (short vs long)
    ema_fast = _ema(closes, span=12)[-1]
    ema_slow = _ema(closes, span=48)[-1]
    momentum = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0

    # Markov first-order: P(up | last=up), P(up | last=down)
    p_up_given_up, p_up_given_down = _markov_up_probs(rets)
    last_up = rets[-1] >= 0
    markov_p_up = p_up_given_up if last_up else p_up_given_down

    # Realised vol (per-bar)
    vol = float(np.std(rets[-60:])) if len(rets) >= 60 else float(np.std(rets) or 1e-6)

    # Combine: 60% Markov, 40% momentum-squashed
    momentum_p_up = 1 / (1 + math.exp(-momentum * 80))
    blended = 0.6 * markov_p_up + 0.4 * momentum_p_up
    blended = max(0.05, min(0.95, blended))

    direction: str
    if blended > 0.55:
        direction = "UP"
    elif blended < 0.45:
        direction = "DOWN"
    else:
        direction = "FLAT"

    # Confidence rises with sample size and falls with vol
    conf = min(1.0, (len(rets) / 500) * (1 - min(0.5, vol * 200)))

    return Prediction(
        asset=series.asset,
        horizon_minutes=horizon,
        direction=direction,  # type: ignore[arg-type]
        prob_up=round(float(blended), 4),
        confidence=round(float(conf), 3),
        model_name="markov-ema-fallback",
        rationale=(
            f"momentum={momentum:+.4%} | markov_p_up={markov_p_up:.3f} "
            f"| vol={vol:.4f} | blended_p_up={blended:.3f}"
        ),
    )


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def _markov_up_probs(rets: np.ndarray) -> Tuple[float, float]:
    """Return (P(up|prev_up), P(up|prev_down)) with Laplace smoothing."""
    if len(rets) < 3:
        return 0.5, 0.5
    prev = rets[:-1] >= 0
    curr = rets[1:] >= 0
    up_to_up = int(np.sum(prev & curr))
    up_to_dn = int(np.sum(prev & ~curr))
    dn_to_up = int(np.sum(~prev & curr))
    dn_to_dn = int(np.sum(~prev & ~curr))
    p_up_g_up = (up_to_up + 1) / (up_to_up + up_to_dn + 2)
    p_up_g_dn = (dn_to_up + 1) / (dn_to_up + dn_to_dn + 2)
    return float(p_up_g_up), float(p_up_g_dn)
