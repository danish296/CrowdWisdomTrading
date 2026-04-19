"""
RiskAgent: combines markets + predictions and applies Kelly sizing,
respecting bankroll, max-fraction cap, and minimum-edge filter.

Also runs an internal-arbitrage scan when multi-horizon predictions are
available — flags disagreements between 5-min and 15-min views.
"""
from __future__ import annotations

from typing import Dict, List

from core.logger import audit
from core.types import Market, Prediction, RiskDecision
from tools import kelly

from .base import Agent


class RiskAgent(Agent):
    name = "risk"
    role = "risk-management"

    def run(
        self,
        markets: List[Market],
        predictions_by_asset: Dict[str, List[Prediction]],
    ) -> List[RiskDecision]:
        decisions: List[RiskDecision] = []
        for m in markets:
            preds = predictions_by_asset.get(m.asset, [])
            # Pick the prediction whose horizon is closest to the market's
            primary = (
                min(preds, key=lambda p: abs(p.horizon_minutes - m.horizon_minutes))
                if preds
                else None
            )
            if primary is None:
                self.log.warning("No prediction for %s — skipping market %s", m.asset, m.market_id)
                continue
            d = kelly.decide(m, primary)
            decisions.append(d)
            self.log.info(
                "[agent.risk]· %s/%s side=%s edge=%+.3f stake=$%.2f EV=$%+.2f — %s",
                m.venue,
                m.asset,
                d.side,
                d.edge,
                d.stake_usd,
                d.expected_value_usd,
                d.notes,
            )
            audit(
                "risk_decision",
                venue=m.venue,
                asset=m.asset,
                side=d.side,
                edge=d.edge,
                stake_usd=d.stake_usd,
                ev_usd=d.expected_value_usd,
            )
        self._scan_arbitrage(predictions_by_asset)
        return decisions

    def _scan_arbitrage(self, preds_by_asset: Dict[str, List[Prediction]]) -> None:
        for asset, preds in preds_by_asset.items():
            shorts = [p.prob_up for p in preds if p.horizon_minutes <= 5]
            longs = [p for p in preds if p.horizon_minutes >= 15]
            if not shorts or not longs:
                continue
            diff, note = kelly.arbitrage_score(shorts, longs[0].prob_up)
            if abs(diff) >= 0.05:
                self.log.info(
                    "[agent.risk][warn]ARB %s diff=%+.3f → %s", asset, diff, note
                )
                audit("arbitrage", asset=asset, diff=diff, note=note)
