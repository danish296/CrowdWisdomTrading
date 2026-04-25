"""
PredictionAgent: turns OHLC series into Up/Down predictions using Kronos
(when available) or a Markov + EMA fallback. Also produces multi-horizon
predictions to enable internal arbitrage detection.

When the predictor mode is "ensemble" we additionally run each engine
on its own and audit them separately, so the live audit ticker shows
both signals side by side and the user can see when Kronos and the
statistical baseline disagree.
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
        choice = kronos_predictor.resolve_predictor_choice()
        out: Dict[str, List[Prediction]] = {}
        for asset, series in series_by_asset.items():
            preds: List[Prediction] = []
            for h in horizons_minutes:
                # In ensemble mode, audit each leg separately so the desk
                # can show "kronos says X, stat says Y, blended Z".
                if choice == "ensemble":
                    self._audit_ensemble_legs(asset, series, h)

                p = kronos_predictor.predict_next_move(series, horizon_minutes=h)
                preds.append(p)
                self.log.info(
                    "[agent.predict]\u00b7 %s @%dm \u2192 %s p_up=%.3f conf=%.2f (%s)",
                    asset, h, p.direction, p.prob_up, p.confidence, p.model_name,
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

    def _audit_ensemble_legs(self, asset: str, series: PriceSeries, h: int) -> None:
        """Best-effort: also record what each engine *individually* said
        on this anchor, so the audit ticker has both lines."""
        for leg in ("statistical", "kronos"):
            try:
                lp = kronos_predictor.predict_next_move(
                    series, horizon_minutes=h, model=leg
                )
                audit(
                    "prediction_leg",
                    asset=asset,
                    horizon=h,
                    direction=lp.direction,
                    prob_up=lp.prob_up,
                    model=lp.model_name,
                )
            except Exception as exc:  # noqa: BLE001
                # Kronos may not be installed; record the failure once
                # rather than spamming the ticker.
                audit(
                    "prediction_leg_error",
                    asset=asset,
                    horizon=h,
                    leg=leg,
                    error=str(exc)[:160],
                )
