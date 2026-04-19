"""
Central configuration. Reads .env once; everything else imports from here.
Every value has a safe default so the agent loop runs in DEMO mode out of
the box.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)


def _env(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return val if val not in (None, "") else default


def _envf(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _envi(key: str, default: int) -> int:
    try:
        return int(float(_env(key, str(default))))
    except (TypeError, ValueError):
        return default


def _envlist(key: str, default: List[str]) -> List[str]:
    raw = _env(key, "")
    if not raw:
        return list(default)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


@dataclass
class Settings:
    # LLM
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    openrouter_model: str = field(
        default_factory=lambda: _env(
            "OPENROUTER_MODEL", "nousresearch/hermes-3-llama-3.1-405b:free"
        )
    )
    openrouter_referer: str = field(
        default_factory=lambda: _env("OPENROUTER_REFERER", "https://localhost")
    )
    openrouter_title: str = field(
        default_factory=lambda: _env("OPENROUTER_TITLE", "CryptoAdsAgents")
    )

    # Apify
    apify_token: str = field(default_factory=lambda: _env("APIFY_TOKEN"))
    apify_polymarket_actor: str = field(
        default_factory=lambda: _env("APIFY_POLYMARKET_ACTOR", "apify/web-scraper")
    )
    apify_kalshi_actor: str = field(
        default_factory=lambda: _env("APIFY_KALSHI_ACTOR", "apify/web-scraper")
    )

    # Risk
    bankroll_usd: float = field(default_factory=lambda: _envf("BANKROLL_USD", 1000.0))
    max_kelly_fraction: float = field(
        default_factory=lambda: _envf("MAX_KELLY_FRACTION", 0.25)
    )
    min_edge: float = field(default_factory=lambda: _envf("MIN_EDGE", 0.02))

    # Universe
    assets: List[str] = field(
        default_factory=lambda: _envlist("ASSETS", ["BTC", "ETH"])
    )

    # Cadence
    loop_seconds: int = field(default_factory=lambda: _envi("LOOP_SECONDS", 60))

    # Dashboard
    dashboard_host: str = field(
        default_factory=lambda: _env("DASHBOARD_HOST", "127.0.0.1")
    )
    dashboard_port: int = field(default_factory=lambda: _envi("DASHBOARD_PORT", 8765))

    # Paths
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    logs_dir: Path = ROOT / "logs"

    @property
    def has_llm(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def has_apify(self) -> bool:
        return bool(self.apify_token)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
