"""
PredictionAgent: turns OHLC series into Up/Down predictions using Kronos
(when available) or a Markov + EMA fallback. Also produces multi-horizon
predictions to enable internal arbitrage detection.
"""
from __future__ import annotations

from typing import Dict, List

from core.logger import audit
from core.types import Prediction, PriceSeries
from tools import kronos_predictor

from .base import Agent


class PredictionAgent(Agent):
    name = "predict"
    role = "prediction"

    def run(
        self,
        series_by_asset: Dict[str, PriceSeries],
        horizons_minutes: List[int] = [5],
    ) -> Dict[str, List[Prediction]]:
        out: Dict[str, List[Prediction]] = {}
        for asset, series in series_by_asset.items():
            preds: List[Prediction] = []
            for h in horizons_minutes:
                p = kronos_predictor.predict_next_move(series, horizon_minutes=h)
                preds.append(p)
                self.log.info(
                    "[agent.predict]· %s @%dm → %s p_up=%.3f conf=%.2f (%s)",
                    asset,
                    h,
                    p.direction,
                    p.prob_up,
                    p.confidence,
                    p.model_name,
                )
                audit(
                    "prediction",
                    asset=asset,
                    horizon=h,
                    direction=p.direction,
                    prob_up=p.prob_up,
                    model=p.model_name,
                )
            out[asset] = preds
        return out
