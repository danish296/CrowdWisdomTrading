"""
MarketSearchAgent: pulls short-horizon BTC/ETH binary markets from
Polymarket and Kalshi in parallel, deduplicates, and returns the top
candidates ranked by liquidity.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List

from core.logger import audit
from core.types import Market
from tools import kalshi, polymarket

from .base import Agent


class MarketSearchAgent(Agent):
    name = "search"
    role = "market-search"

    def run(
        self,
        assets: List[str],
        horizon_minutes: int = 5,
        limit_per_asset: int = 3,
    ) -> List[Market]:
        self.log.info(
            "[agent.search]Searching %s on Polymarket + Kalshi (horizon=%dm)",
            assets,
            horizon_minutes,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            poly_f = pool.submit(
                polymarket.search_markets, assets, horizon_minutes, limit_per_asset
            )
            kal_f = pool.submit(
                kalshi.search_markets, assets, horizon_minutes, limit_per_asset
            )
            poly = poly_f.result()
            kal = kal_f.result()

        markets = poly + kal
        markets.sort(key=lambda m: m.volume_usd, reverse=True)
        audit(
            "search_done",
            n_markets=len(markets),
            polymarket=len(poly),
            kalshi=len(kal),
        )
        for m in markets:
            self.log.info(
                "[agent.search]· %s/%s yes=%.2f vol=$%.0f — %s",
                m.venue,
                m.asset,
                m.yes_price,
                m.volume_usd,
                m.question[:80],
            )
        return markets
