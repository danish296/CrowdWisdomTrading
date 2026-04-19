"""
Orchestrator: runs the full agent loop on a schedule.

Pipeline:
    1.  MarketSearchAgent  -> markets (Polymarket + Kalshi)
    2.  DataFetchAgent     -> 1000 OHLC bars per asset (Apify/Binance)
    3.  PredictionAgent    -> multi-horizon predictions (5m + 15m)
    4.  RiskAgent          -> Kelly-sized decisions + arbitrage scan
    5.  FeedbackAgent      -> grade prior cycle + Hermes self-critique
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from config import settings
from core.logger import audit, console, get_logger
from core.memory import append_cycle
from core.types import CycleResult

from .data_fetch_agent import DataFetchAgent
from .feedback_agent import FeedbackAgent
from .market_search_agent import MarketSearchAgent
from .prediction_agent import PredictionAgent
from .risk_agent import RiskAgent

log = get_logger("orch")


class Orchestrator:
    def __init__(self) -> None:
        self.search = MarketSearchAgent()
        self.data = DataFetchAgent()
        self.predict = PredictionAgent()
        self.risk = RiskAgent()
        self.feedback = FeedbackAgent()
        self.last_cycle: Optional[CycleResult] = None

    def run_once(self) -> CycleResult:
        started = datetime.now(timezone.utc)
        errors: list[str] = []
        markets, predictions, decisions = [], [], []
        feedback_text: Optional[str] = None
        series_by_asset = {}

        console.rule(
            f"[agent.orch]CYCLE {started.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        try:
            markets = self.search.run(settings.assets, horizon_minutes=5)
        except Exception as exc:  # noqa: BLE001
            log.exception("search failed")
            errors.append(f"search: {exc}")

        try:
            series_by_asset = self.data.run(settings.assets, interval="1m", limit=1000)
        except Exception as exc:  # noqa: BLE001
            log.exception("data fetch failed")
            errors.append(f"data: {exc}")

        try:
            preds_by_asset = self.predict.run(
                series_by_asset, horizons_minutes=[5, 15]
            )
            # Flatten for storage
            predictions = [p for ps in preds_by_asset.values() for p in ps]
        except Exception as exc:  # noqa: BLE001
            log.exception("prediction failed")
            errors.append(f"predict: {exc}")
            preds_by_asset = {}

        try:
            decisions = self.risk.run(markets, preds_by_asset)
        except Exception as exc:  # noqa: BLE001
            log.exception("risk failed")
            errors.append(f"risk: {exc}")

        try:
            feedback_text = self.feedback.run(
                self.last_cycle, series_by_asset, decisions
            )
            log.info("[agent.feedback]reflection: %s", feedback_text)
        except Exception as exc:  # noqa: BLE001
            log.exception("feedback failed")
            errors.append(f"feedback: {exc}")

        finished = datetime.now(timezone.utc)
        cycle = CycleResult(
            started_at=started,
            finished_at=finished,
            markets=markets,
            predictions=predictions,
            decisions=decisions,
            feedback=feedback_text,
            errors=errors,
        )
        append_cycle(cycle)
        audit(
            "cycle_done",
            duration_s=(finished - started).total_seconds(),
            n_markets=len(markets),
            n_decisions=len(decisions),
            n_errors=len(errors),
        )
        self.last_cycle = cycle
        return cycle

    def loop_forever(self) -> None:
        log.info("[agent.orch]Starting orchestrator loop (every %ds)", settings.loop_seconds)
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                log.info("[agent.orch]Stopped by user.")
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Cycle crashed: %s", exc)
            time.sleep(settings.loop_seconds)
