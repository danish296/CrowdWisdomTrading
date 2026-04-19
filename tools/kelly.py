"""
Kelly criterion sizing for binary prediction markets.

Given a market that pays $1 for YES and the model's true probability
estimate `p`, the standard Kelly fraction for the YES contract priced at
`q` (so net odds b = (1 - q) / q) is:

    f* = (b * p - (1 - p)) / b
       = (p - q) / (1 - q)        [for YES]

We compute it for both YES and NO sides, take the larger non-negative
edge, cap it by `MAX_KELLY_FRACTION`, multiply by bankroll, and return a
RiskDecision. Anything below MIN_EDGE is SKIP.

References:
- https://mintlify.wiki/joicodev/polymarket-bot/risk/kelly-criterion
- https://managebankroll.com/blog/polymarket-kelly-criterion-position-sizing
"""
from __future__ import annotations

from typing import Tuple

from config import settings
from core.types import Market, Prediction, RiskDecision


def _kelly_fraction(p: float, q: float) -> float:
    """Kelly fraction for buying YES at price q with true prob p."""
    if q <= 0 or q >= 1:
        return 0.0
    b = (1 - q) / q
    f = (b * p - (1 - p)) / b
    return max(0.0, f)


def decide(
    market: Market,
    prediction: Prediction,
    bankroll_usd: float | None = None,
    cap: float | None = None,
    min_edge: float | None = None,
) -> RiskDecision:
    bankroll = bankroll_usd if bankroll_usd is not None else settings.bankroll_usd
    cap = cap if cap is not None else settings.max_kelly_fraction
    min_edge = min_edge if min_edge is not None else settings.min_edge

    p_up = prediction.prob_up
    p_dn = 1.0 - p_up
    q_yes = market.yes_price
    q_no = market.no_price

    yes_edge = p_up - q_yes
    no_edge = p_dn - q_no

    # Pick the side with larger positive edge
    if yes_edge >= no_edge and yes_edge > 0:
        side = "YES"
        edge = yes_edge
        kelly = _kelly_fraction(p_up, q_yes)
        true_p, price = p_up, q_yes
    elif no_edge > 0:
        side = "NO"
        edge = no_edge
        kelly = _kelly_fraction(p_dn, q_no)
        true_p, price = p_dn, q_no
    else:
        return _skip(market, prediction, "no positive edge on either side")

    # Confidence-scaled Kelly: shrink bet if model is unsure
    conf_kelly = kelly * max(0.0, min(1.0, prediction.confidence))
    capped = min(conf_kelly, cap)
    stake = round(capped * bankroll, 2)

    if edge < min_edge:
        return _skip(
            market, prediction, f"edge {edge:.3f} below MIN_EDGE {min_edge:.3f}"
        )

    # Expected value (in $): stake * (p * (1 - price) / price - (1 - p))
    ev_per_dollar = (true_p * (1 - price) / price) - (1 - true_p)
    expected_value = round(stake * ev_per_dollar, 2)

    return RiskDecision(
        market=market,
        prediction=prediction,
        side=side,                                  # type: ignore[arg-type]
        edge=round(edge, 4),
        kelly_fraction=round(kelly, 4),
        capped_fraction=round(capped, 4),
        stake_usd=stake,
        expected_value_usd=expected_value,
        notes=f"true_p={true_p:.3f} price={price:.3f} conf={prediction.confidence:.2f}",
    )


def _skip(market: Market, prediction: Prediction, reason: str) -> RiskDecision:
    return RiskDecision(
        market=market,
        prediction=prediction,
        side="SKIP",
        edge=0.0,
        kelly_fraction=0.0,
        capped_fraction=0.0,
        stake_usd=0.0,
        expected_value_usd=0.0,
        notes=reason,
    )


def arbitrage_score(short_p_ups: list[float], long_p_up: float) -> Tuple[float, str]:
    """
    Internal-arbitrage detector: compare 3 consecutive 5-min predictions
    against a 15-min prediction. If their compounded direction disagrees
    with the 15-min view, that's an arbitrage opportunity to flag.

    Returns (signed_disagreement, note).
    """
    if not short_p_ups:
        return 0.0, "no short predictions"
    avg_short = sum(short_p_ups) / len(short_p_ups)
    diff = avg_short - long_p_up
    if abs(diff) < 0.05:
        return diff, "aligned"
    direction = "BULLISH 5m vs BEARISH 15m" if diff > 0 else "BEARISH 5m vs BULLISH 15m"
    return diff, f"arbitrage candidate: {direction}"
