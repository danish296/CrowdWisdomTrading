"""
Shared Pydantic models that flow between agents. Keeping them in one place
makes the contracts explicit and the orchestrator easy to read.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["UP", "DOWN", "FLAT"]
Venue = Literal["polymarket", "kalshi"]


class Market(BaseModel):
    """A binary market on a 5-minute (or other) crypto question."""
    venue: Venue
    market_id: str
    question: str
    asset: str                       # e.g. "BTC"
    horizon_minutes: int = 5
    yes_price: float                 # implied prob of YES (0..1)
    no_price: float                  # implied prob of NO  (0..1)
    volume_usd: float = 0.0
    url: str = ""

    @property
    def implied_up_prob(self) -> float:
        # By convention "YES" == "price up by close of market"
        return self.yes_price


class OHLCBar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceSeries(BaseModel):
    asset: str
    interval: str                    # "1m", "5m", ...
    bars: List[OHLCBar]
    source: str                      # "binance" / "apify" / "demo"


class Prediction(BaseModel):
    asset: str
    horizon_minutes: int
    direction: Direction
    prob_up: float                   # model's P(up)
    confidence: float                # 0..1, model self-confidence
    model_name: str
    rationale: str = ""


class RiskDecision(BaseModel):
    market: Market
    prediction: Prediction
    side: Literal["YES", "NO", "SKIP"]
    edge: float                      # model_prob - implied_prob (signed for chosen side)
    kelly_fraction: float            # raw Kelly
    capped_fraction: float           # after MAX_KELLY_FRACTION
    stake_usd: float
    expected_value_usd: float
    notes: str = ""


class FeedbackRecord(BaseModel):
    """Stored after a market resolves so Hermes can self-improve."""
    market_id: str
    venue: Venue
    asset: str
    predicted: Direction
    realized: Direction
    model_prob_up: float
    realized_return_pct: float
    pnl_usd: float
    correct: bool
    ts: float = Field(default_factory=lambda: datetime.utcnow().timestamp())
    note: str = ""


class CycleResult(BaseModel):
    """One full pass of the orchestrator loop."""
    started_at: datetime
    finished_at: datetime
    markets: List[Market]
    predictions: List[Prediction]
    decisions: List[RiskDecision]
    feedback: Optional[str] = None
    errors: List[str] = []
