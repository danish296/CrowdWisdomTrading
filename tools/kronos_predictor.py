"""
Direction predictor.

Tries Kronos (NeoQuasar/Kronos) when its dependencies are installed, with
configurable model size (mini / small / base), device, and an optional
local path for offline use. Falls back to a robust statistical predictor
(Markov first-order chain blended with EMA momentum) when Kronos is not
available or fails at runtime.

Both paths return the same Prediction shape so the orchestrator and the
backtester are agnostic to which one ran.

How to enable Kronos
--------------------
Two install paths are supported.

A. Pip-style. Some Kronos forks publish a `kronos` package; if one is
   installed in your environment we just import it.

B. Repo-style (recommended for the official NeoQuasar/Kronos). Clone
   the repo locally and point an env var at it:

     git clone https://github.com/NeoQuasar/Kronos.git
     pip install -r Kronos/requirements.txt
     export KRONOS_REPO_PATH=/abs/path/to/Kronos

   The wrapper will prepend that directory to sys.path and import
   `from model import Kronos, KronosTokenizer, KronosPredictor`,
   which is the canonical entry point in the official repo.

Model selection (env or argument)
---------------------------------
KRONOS_MODEL_SIZE       mini | small | base       (default: small)
KRONOS_TOKENIZER        repo id or local path     (default: NeoQuasar/Kronos-Tokenizer-base)
KRONOS_MODEL_PATH       local path to model dir   (overrides size if set)
KRONOS_DEVICE           cpu | cuda | cuda:0 ...   (default: auto-detect)
KRONOS_MAX_CONTEXT      int                        (default: 512)
KRONOS_T                float                      (default: 1.0)
KRONOS_TOP_P            float                      (default: 0.9)
KRONOS_SAMPLE_COUNT     int                        (default: 1)
"""
from __future__ import annotations

import importlib
import math
import os
import sys
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, List, Optional, Tuple

import numpy as np

from core.logger import get_logger
from core.types import OHLCBar, Prediction, PriceSeries

log = get_logger("tool.predict")


# --------------------------------------------------------------------------- #
# Public                                                                      #
# --------------------------------------------------------------------------- #


# Map a friendly size label to the canonical Hugging Face repo id from the
# official NeoQuasar/Kronos project. If the user passes their own
# KRONOS_MODEL_PATH we use that instead.
_KRONOS_MODELS = {
    "mini":  "NeoQuasar/Kronos-mini",
    "small": "NeoQuasar/Kronos-small",
    "base":  "NeoQuasar/Kronos-base",
}
_DEFAULT_TOKENIZER = "NeoQuasar/Kronos-Tokenizer-base"


def predict_next_move(
    series: PriceSeries,
    horizon_minutes: int = 5,
    *,
    model: Optional[str] = None,
) -> Prediction:
    """Return a direction + probability for the next `horizon_minutes`.

    `model` overrides the global default and accepts:
        "statistical"   force the Markov+EMA fallback
        "kronos"        use the configured Kronos size
        "kronos-mini" / "kronos-small" / "kronos-base"
    """
    bars = series.bars
    if len(bars) < 30:
        return Prediction(
            asset=series.asset,
            horizon_minutes=horizon_minutes,
            direction="FLAT",
            prob_up=0.5,
            confidence=0.0,
            model_name="insufficient-data",
            rationale="Need >= 30 bars to predict.",
        )

    requested = (model or os.getenv("PREDICTOR_DEFAULT", "")).strip().lower()
    if requested in ("statistical", "stat", "baseline"):
        return _statistical_predict(series, horizon_minutes)

    explicit_size: Optional[str] = None
    explicit_kronos = requested.startswith("kronos")
    if explicit_kronos and "-" in requested:
        explicit_size = requested.split("-", 1)[1]

    if explicit_kronos:
        # User explicitly asked for Kronos -- never silently fall back, so the
        # backtest comparison is honest. The caller (backtest.engine) catches
        # this and counts it as a failure for that model.
        return _kronos_predict(series, horizon_minutes, explicit_size=explicit_size)

    if _kronos_available():
        try:
            return _kronos_predict(series, horizon_minutes, explicit_size=explicit_size)
        except Exception as exc:  # noqa: BLE001
            log.warning("Kronos predictor failed (%s) -- using statistical fallback", exc)

    return _statistical_predict(series, horizon_minutes)


def list_available_predictors() -> List[dict]:
    """Used by the dashboard to populate the model picker."""
    out: List[dict] = [
        {
            "id": "statistical",
            "label": "Statistical (Markov + EMA, baseline)",
            "available": True,
            "device": "cpu",
        }
    ]
    kronos_ok = _kronos_available()
    for size, repo in _KRONOS_MODELS.items():
        out.append({
            "id": f"kronos-{size}",
            "label": f"Kronos {size} ({repo})",
            "available": kronos_ok,
            "device": _resolve_device(),
            "params_m": {"mini": 4.1, "small": 24.7, "base": 102.3}[size],
        })
    return out


# --------------------------------------------------------------------------- #
# Kronos (optional)                                                           #
# --------------------------------------------------------------------------- #


def _resolve_device() -> str:
    env = os.getenv("KRONOS_DEVICE", "").strip().lower()
    if env:
        return env
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _kronos_available() -> bool:
    """True iff we can import torch AND the Kronos classes."""
    try:
        importlib.import_module("torch")
    except ImportError:
        return False
    try:
        _import_kronos_classes()
        return True
    except Exception:  # noqa: BLE001
        return False


def _import_kronos_classes() -> Tuple[Any, Any, Any]:
    """Two import strategies, in order:

    1. Pip-installed `kronos` package.
    2. Repo clone pointed to by KRONOS_REPO_PATH (the canonical layout
       of the official NeoQuasar/Kronos repo, where the classes live in
       a top-level `model` module).
    """
    last_err: Optional[Exception] = None

    try:
        kronos_pkg = importlib.import_module("kronos")
        return (
            getattr(kronos_pkg, "Kronos"),
            getattr(kronos_pkg, "KronosTokenizer"),
            getattr(kronos_pkg, "KronosPredictor"),
        )
    except Exception as exc:  # noqa: BLE001
        last_err = exc

    repo_path = os.getenv("KRONOS_REPO_PATH", "").strip()
    if repo_path and os.path.isdir(repo_path):
        if repo_path not in sys.path:
            sys.path.insert(0, repo_path)
        try:
            mdl = importlib.import_module("model")
            return (
                getattr(mdl, "Kronos"),
                getattr(mdl, "KronosTokenizer"),
                getattr(mdl, "KronosPredictor"),
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc

    raise ImportError(
        "Could not import Kronos. Either `pip install kronos` (if a fork "
        "publishes one), or clone https://github.com/NeoQuasar/Kronos and set "
        "KRONOS_REPO_PATH to its absolute path. Original error: "
        + repr(last_err)
    )


# Module-level cache so we never reload weights inside a hot loop.
_PREDICTOR_CACHE: dict = {}
_PREDICTOR_LOCK = threading.Lock()


def _get_predictor(size: str):
    key = (size, _resolve_device())
    if key in _PREDICTOR_CACHE:
        return _PREDICTOR_CACHE[key]

    with _PREDICTOR_LOCK:
        if key in _PREDICTOR_CACHE:
            return _PREDICTOR_CACHE[key]

        Kronos, KronosTokenizer, KronosPredictor = _import_kronos_classes()

        tokenizer_id = os.getenv("KRONOS_TOKENIZER", _DEFAULT_TOKENIZER).strip()
        model_path = os.getenv("KRONOS_MODEL_PATH", "").strip()
        if model_path:
            model_id = model_path
        else:
            model_id = _KRONOS_MODELS.get(size, _KRONOS_MODELS["small"])

        device = _resolve_device()
        max_context = int(os.getenv("KRONOS_MAX_CONTEXT", "512") or "512")

        log.info(
            "Kronos: loading tokenizer=%s model=%s device=%s max_context=%d",
            tokenizer_id, model_id, device, max_context,
        )
        tokenizer = KronosTokenizer.from_pretrained(tokenizer_id)
        model = Kronos.from_pretrained(model_id)
        predictor = KronosPredictor(
            model=model,
            tokenizer=tokenizer,
            device=device,
            max_context=max_context,
        )
        _PREDICTOR_CACHE[key] = (predictor, model_id)
        return _PREDICTOR_CACHE[key]


def _series_to_dataframe(series: PriceSeries):
    """Convert our PriceSeries into the OHLCV(+amount) pandas DataFrame
    that the Kronos predictor expects."""
    import pandas as pd

    rows = [
        {
            "timestamps": b.ts,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            # `amount` is the close * volume proxy used in the official
            # repo's example; Kronos tolerates either presence or absence
            # but providing it improves token quality.
            "amount": b.close * b.volume,
        }
        for b in series.bars
    ]
    df = pd.DataFrame(rows)
    df["timestamps"] = pd.to_datetime(df["timestamps"], utc=True)
    return df


def _interval_to_minutes(interval: str) -> int:
    s = interval.strip().lower()
    if s.endswith("m"):
        return max(1, int(s[:-1] or 1))
    if s.endswith("h"):
        return max(1, int(s[:-1] or 1)) * 60
    if s.endswith("d"):
        return max(1, int(s[:-1] or 1)) * 60 * 24
    try:
        return max(1, int(s))
    except ValueError:
        return 1


def _kronos_predict(
    series: PriceSeries,
    horizon_minutes: int,
    *,
    explicit_size: Optional[str] = None,
) -> Prediction:
    import pandas as pd

    size = (explicit_size or os.getenv("KRONOS_MODEL_SIZE", "small")).strip().lower()
    if size not in _KRONOS_MODELS:
        size = "small"

    predictor, model_id = _get_predictor(size)

    df = _series_to_dataframe(series)
    bar_minutes = _interval_to_minutes(series.interval)
    pred_len = max(1, math.ceil(horizon_minutes / bar_minutes))

    # Hard cap on context for performance / memory.
    max_ctx = int(os.getenv("KRONOS_MAX_CONTEXT", "512") or "512")
    df_in = df.tail(max_ctx).reset_index(drop=True)

    x_timestamp = df_in["timestamps"]
    last_ts = x_timestamp.iloc[-1]
    y_timestamp = pd.Series(
        [last_ts + pd.Timedelta(minutes=bar_minutes * (i + 1)) for i in range(pred_len)]
    )

    T_temp = float(os.getenv("KRONOS_T", "1.0") or "1.0")
    top_p = float(os.getenv("KRONOS_TOP_P", "0.9") or "0.9")
    sample_count = int(os.getenv("KRONOS_SAMPLE_COUNT", "1") or "1")

    pred_df = predictor.predict(
        df=df_in[["open", "high", "low", "close", "volume", "amount"]],
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=pred_len,
        T=T_temp,
        top_p=top_p,
        sample_count=sample_count,
        verbose=False,
    )

    last_close = float(df_in["close"].iloc[-1])
    target_close = float(pred_df["close"].iloc[-1])
    chg = (target_close - last_close) / last_close if last_close else 0.0

    # Squash signed % change into [0,1] via logistic with a slope tuned
    # for short-horizon crypto (a 1% move maps to ~0.65 prob_up).
    prob_up = float(1.0 / (1.0 + math.exp(-chg * 60.0)))
    prob_up = max(0.01, min(0.99, prob_up))

    direction = "UP" if prob_up > 0.55 else "DOWN" if prob_up < 0.45 else "FLAT"
    confidence = min(1.0, abs(chg) * 80.0)

    return Prediction(
        asset=series.asset,
        horizon_minutes=horizon_minutes,
        direction=direction,  # type: ignore[arg-type]
        prob_up=round(prob_up, 4),
        confidence=round(float(confidence), 3),
        model_name=f"kronos-{size}",
        rationale=(
            f"Kronos[{size}] forecast Δ={chg:+.4%} over "
            f"{pred_len} bars × {bar_minutes}m"
        ),
    )


# --------------------------------------------------------------------------- #
# Statistical fallback                                                        #
# --------------------------------------------------------------------------- #


def _statistical_predict(series: PriceSeries, horizon: int) -> Prediction:
    bars: List[OHLCBar] = series.bars
    closes = np.array([b.close for b in bars], dtype=np.float64)
    rets = np.diff(closes) / closes[:-1]

    ema_fast = _ema(closes, span=12)[-1]
    ema_slow = _ema(closes, span=48)[-1]
    momentum = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0

    p_up_given_up, p_up_given_down = _markov_up_probs(rets)
    last_up = rets[-1] >= 0
    markov_p_up = p_up_given_up if last_up else p_up_given_down

    vol = float(np.std(rets[-60:])) if len(rets) >= 60 else float(np.std(rets) or 1e-6)

    momentum_p_up = 1.0 / (1.0 + math.exp(-momentum * 80.0))
    blended = 0.6 * markov_p_up + 0.4 * momentum_p_up
    blended = max(0.05, min(0.95, blended))

    if blended > 0.55:
        direction = "UP"
    elif blended < 0.45:
        direction = "DOWN"
    else:
        direction = "FLAT"

    conf = min(1.0, (len(rets) / 500.0) * (1.0 - min(0.5, vol * 200.0)))

    return Prediction(
        asset=series.asset,
        horizon_minutes=horizon,
        direction=direction,  # type: ignore[arg-type]
        prob_up=round(float(blended), 4),
        confidence=round(float(conf), 3),
        model_name="markov-ema-fallback",
        rationale=(
            f"momentum={momentum:+.4%} | markov_p_up={markov_p_up:.3f} "
            f"| vol={vol:.4f} | blended_p_up={blended:.3f}"
        ),
    )


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def _markov_up_probs(rets: np.ndarray) -> Tuple[float, float]:
    """(P(up|prev_up), P(up|prev_down)) with Laplace smoothing."""
    if len(rets) < 3:
        return 0.5, 0.5
    prev = rets[:-1] >= 0
    curr = rets[1:] >= 0
    up_to_up = int(np.sum(prev & curr))
    up_to_dn = int(np.sum(prev & ~curr))
    dn_to_up = int(np.sum(~prev & curr))
    dn_to_dn = int(np.sum(~prev & ~curr))
    p_up_g_up = (up_to_up + 1) / (up_to_up + up_to_dn + 2)
    p_up_g_dn = (dn_to_up + 1) / (dn_to_up + dn_to_dn + 2)
    return float(p_up_g_up), float(p_up_g_dn)
