"""
DataFetchAgent: for each asset in the universe, pulls the last N OHLC
bars (default 1000) using Apify -> Binance -> synthetic fallback.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from core.logger import audit
from core.types import PriceSeries
from tools import apify_scraper

from .base import Agent


class DataFetchAgent(Agent):
    name = "data"
    role = "data-fetch"

    def run(
        self,
        assets: List[str],
        interval: str = "1m",
        limit: int = 1000,
    ) -> Dict[str, PriceSeries]:
        self.log.info(
            "[agent.data]Fetching %d %s bars for %s", limit, interval, assets
        )
        out: Dict[str, PriceSeries] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(assets))) as pool:
            futures = {
                pool.submit(apify_scraper.fetch_bars, a, interval, limit): a
                for a in assets
            }
            for fut in as_completed(futures):
                asset = futures[fut]
                try:
                    series = fut.result()
                    out[asset] = series
                    self.log.info(
                        "[agent.data]· %s ← %d bars from %s",
                        asset,
                        len(series.bars),
                        series.source,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.log.error("Data fetch for %s failed: %s", asset, exc)
                    audit("data_error", asset=asset, error=str(exc))
        return out
