"""Walk-forward backtester for the prediction stack.

This package fetches real OHLC history from a public venue, runs each
configured predictor (statistical baseline + Kronos when available)
through a sliding-window protocol, and computes calibration, hit-rate,
risk-adjusted P&L and Diebold-Mariano significance side by side.

No synthetic data is allowed in here. If the network is unreachable the
backtest fails loudly rather than fabricating bars.
"""
from .engine import BacktestSpec, run_backtest
from .store import (
    BacktestRecord,
    delete_backtest,
    list_backtests,
    load_backtest,
    save_backtest,
)

__all__ = [
    "BacktestSpec",
    "BacktestRecord",
    "delete_backtest",
    "list_backtests",
    "load_backtest",
    "run_backtest",
    "save_backtest",
]
