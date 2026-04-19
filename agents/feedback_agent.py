"""
FeedbackAgent: closes the loop. After each cycle it inspects the prior
cycle's predictions against the now-realized market move, records the
result in feedback memory, and (when an LLM is available) asks Hermes
for a one-paragraph self-critique that the next cycle's narrators read.
"""
from __future__ import annotations

from typing import Dict, List

from core.llm import HermesTool, get_client, run_hermes_loop
from core.logger import audit
from core.memory import (
    append_feedback,
    hit_rate,
    load_recent_feedback,
    total_pnl,
)
from core.types import (
    CycleResult,
    FeedbackRecord,
    PriceSeries,
    RiskDecision,
)

from .base import Agent


class FeedbackAgent(Agent):
    name = "feedback"
    role = "feedback-loop"

    # ------------------------------------------------------------------ #
    # Realized P&L scoring                                               #
    # ------------------------------------------------------------------ #

    def score_previous(
        self,
        prior: CycleResult | None,
        current_series: Dict[str, PriceSeries],
    ) -> List[FeedbackRecord]:
        if prior is None:
            return []
        records: List[FeedbackRecord] = []
        for d in prior.decisions:
            if d.side == "SKIP":
                continue
            series = current_series.get(d.market.asset)
            if not series or len(series.bars) < 2:
                continue
            # Use the close at the time of the prior cycle vs the latest close
            prior_close = series.bars[-min(d.market.horizon_minutes + 1, len(series.bars))].close
            now_close = series.bars[-1].close
            ret = (now_close - prior_close) / prior_close if prior_close else 0.0
            realized = "UP" if ret > 0 else "DOWN" if ret < 0 else "FLAT"
            won = (
                (d.side == "YES" and realized == "UP")
                or (d.side == "NO" and realized == "DOWN")
            )
            # Binary contract P&L: stake * (1 - price)/price on win, -stake on loss
            price = (
                d.market.yes_price if d.side == "YES" else d.market.no_price
            )
            pnl = (
                d.stake_usd * ((1 - price) / price) if won else -d.stake_usd
            )
            rec = FeedbackRecord(
                market_id=d.market.market_id,
                venue=d.market.venue,
                asset=d.market.asset,
                predicted=d.prediction.direction,
                realized=realized,  # type: ignore[arg-type]
                model_prob_up=d.prediction.prob_up,
                realized_return_pct=round(ret * 100, 4),
                pnl_usd=round(pnl, 2),
                correct=won,
                note=d.notes,
            )
            append_feedback(rec)
            records.append(rec)
            self.log.info(
                "[agent.feedback]· %s/%s pred=%s real=%s pnl=$%+.2f",
                rec.venue,
                rec.asset,
                rec.predicted,
                rec.realized,
                rec.pnl_usd,
            )
            audit(
                "feedback",
                venue=rec.venue,
                asset=rec.asset,
                correct=rec.correct,
                pnl_usd=rec.pnl_usd,
            )
        return records

    # ------------------------------------------------------------------ #
    # Hermes self-critique                                               #
    # ------------------------------------------------------------------ #

    def reflect(self, decisions: List[RiskDecision]) -> str:
        recent = load_recent_feedback(limit=50)
        hr = hit_rate(recent)
        pnl = total_pnl(recent)
        summary = (
            f"hit_rate={hr:.1%} (n={len(recent)}) total_pnl=${pnl:+.2f}. "
            f"latest_decisions={len(decisions)}, "
            f"actionable={sum(1 for d in decisions if d.side != 'SKIP')}."
        )
        if get_client() is None:
            return summary
        try:
            tools = [
                HermesTool(
                    name="get_recent_stats",
                    description="Return recent hit-rate and pnl numbers.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    fn=lambda: {
                        "hit_rate": hr,
                        "n": len(recent),
                        "total_pnl_usd": pnl,
                    },
                )
            ]
            answer = run_hermes_loop(
                system_prompt=(
                    "You are the feedback agent in a crypto-prediction pipeline. "
                    "Given recent stats, give a 2-3 sentence critique that the "
                    "next loop's prediction agent should consider. Be specific, "
                    "no fluff, no markdown headers."
                ),
                user_prompt=summary,
                tools=tools,
                max_iters=2,
            )
            return answer or summary
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Hermes reflection failed: %s", exc)
            return summary

    def run(
        self,
        prior: CycleResult | None,
        current_series: Dict[str, PriceSeries],
        decisions: List[RiskDecision],
    ) -> str:
        self.score_previous(prior, current_series)
        return self.reflect(decisions)
