<div align="center">

# CrowdWisdomTrading

### An agentic Python desk for short-horizon prediction markets.

Searches **Polymarket** + **Kalshi** for 5-minute BTC / ETH binary markets,
pulls 1 000-bar OHLC streams via **Apify** (Binance fallback), forecasts the
next move with **Kronos** (Markov + EMA fallback), sizes positions with the
**Kelly criterion**, and closes the loop with a **Hermes** self-critique.

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-Hermes--3-f5b400)](https://openrouter.ai/)
[![Apify](https://img.shields.io/badge/Apify-actors-1F62FE?logo=apify&logoColor=white)](https://apify.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-000000.svg)](#license)

**Built by [Danish Akhtar](https://danishakhtar.tech)** · [GitHub](https://github.com/danish296)

</div>

---

## Table of contents

1. [Why this exists](#why-this-exists)
2. [Pipeline at a glance](#pipeline-at-a-glance)
3. [Project layout](#project-layout)
4. [Quickstart (5 minutes)](#quickstart-5-minutes)
5. [Configuration](#configuration)
6. [Commands](#commands)
7. [Live output (recorded)](#live-output-recorded)
8. [Dashboard](#dashboard) — includes [implementation audit](#implementation-audit)
9. [Backtest workbench](#backtest-workbench)
10. [Kronos integration (local install)](#kronos-integration-local-install)
11. [Architecture deep-dive](#architecture-deep-dive)
12. [Scaling levers](#scaling-levers)
13. [Logging, persistence, observability](#logging-persistence-observability) — includes [predictor and Kronos console log reference](#predictor-and-kronos-console-log-reference)
14. [Testing & smoke verification](#testing--smoke-verification)
15. [Roadmap](#roadmap)
16. [FAQ & honest caveats](#faq--honest-caveats)
17. [Credits & License](#credits--license)

---

## Why this exists

Short-horizon binary markets on Polymarket and Kalshi (e.g. *"Will BTC close
higher in the next 5 minutes?"*) are messy: the markets are spread across two
venues, prices drift in seconds, and historical data has to be stitched
together from multiple feeds. A small army of cooperating agents handles
each part of the pipeline, and a Hermes self-critique loop at the end keeps
the system honest about its own track record.

Out of the box this project runs in **DEMO mode with zero API keys** — it
will degrade gracefully to public Binance OHLC and synthetic markets so the
loop never crashes. Add your `OPENROUTER_API_KEY` and `APIFY_TOKEN` to go
live.

## Pipeline at a glance

```
search ─► data ─► predict ─► risk ─► feedback ─► (next loop)
  │         │        │         │         │
  Poly    Apify    Kronos    Kelly    Hermes
  Kalshi  Binance  Markov+   cap +    grades prior
                   EMA       MIN_EDGE cycle, reflects
```

Each stage is its own agent that can be run in isolation, profiled, or
swapped — `agents/orchestrator.py` is the only file that knows the order.

## Project layout

```
CrowdWisdomTrading/
├── README.md
├── requirements.txt
├── .env.example                          ← every key optional
├── .gitignore
├── config.py                             ← single source of truth
├── main.py                               ← CLI: once / loop / demo / status / dashboard
├── api.py                                ← FastAPI dashboard server
├── core/
│   ├── llm.py                            ← OpenRouter client + Hermes tool-calling loop
│   ├── logger.py                         ← Rich console + JSONL audit (UTF-8 forced on Windows)
│   ├── memory.py                         ← persistent feedback / cycles
│   └── types.py                          ← Pydantic contracts shared across agents
├── tools/
│   ├── polymarket.py                     ← gamma-api + DEMO fallback
│   ├── kalshi.py                         ← elections-api + DEMO fallback
│   ├── apify_scraper.py                  ← Apify → Binance → synthetic
│   ├── kronos_predictor.py               ← Kronos (opt) + Markov + EMA fallback
│   └── kelly.py                          ← Kelly criterion + 5m-vs-15m arbitrage scoring
├── agents/
│   ├── base.py                           ← Hermes-aware base agent
│   ├── market_search_agent.py            ← parallel Polymarket + Kalshi search
│   ├── data_fetch_agent.py               ← thread-pooled OHLC fetcher
│   ├── prediction_agent.py               ← multi-horizon directional forecasts
│   ├── risk_agent.py                     ← Kelly sizing + arbitrage scan
│   ├── feedback_agent.py                 ← grades prior cycle + Hermes self-critique
│   └── orchestrator.py                   ← the 60-second loop
├── backtest/
│   ├── data.py                           ← paginated Bitstamp + Binance fetchers (no synthetic)
│   ├── engine.py                         ← walk-forward simulator, per-anchor trade ledger
│   ├── metrics.py                        ← Brier / log-loss / hit / Sharpe / DD / DM
│   └── store.py                          ← JSON persistence under data/backtests/<id>.json
└── dashboard/
    ├── templates/
    │   ├── index.html                    ← editorial-brutalist HTML (live desk)
    │   └── backtest.html                 ← workbench: spec form + side-by-side report
    └── static/
        ├── styles.css                    ← Fraunces + JetBrains Mono · amber / ink / cream
        ├── app.js                        ← live-desk vanilla JS
        └── backtest.js                   ← Chart.js equity / calibration / track plots
```

## Quickstart (5 minutes)

### 1. Clone

```bash
git clone https://github.com/danish296/CrowdWisdomTrading.git
cd CrowdWisdomTrading
```

### 2. Create a virtualenv and install

```bash
# Windows (PowerShell)
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. (Optional) Configure

```bash
cp .env.example .env
# Open .env and add OPENROUTER_API_KEY / APIFY_TOKEN if you have them.
# Everything is optional — DEMO mode runs without a single key.
```

### 4. Run

```bash
python main.py demo               # 3 cycles, 5 s apart  ← start here
python main.py once               # one cycle then exit
python main.py loop               # continuous loop
python main.py status             # show recent stats
python main.py dashboard          # http://127.0.0.1:8765
```

That's it. You should see a styled banner, market lookups, predictions,
Kelly-sized decisions, and a feedback line within ~20 seconds.

## Configuration

Every value is optional. See `.env.example` for the full list.

| Variable             | Default                                          | Meaning                              |
| -------------------- | ------------------------------------------------ | ------------------------------------ |
| `OPENROUTER_API_KEY` | —                                                | Optional. Enables Hermes narration   |
| `OPENROUTER_MODEL`   | `nousresearch/hermes-3-llama-3.1-405b:free`      | Any OpenRouter model with tool-use   |
| `APIFY_TOKEN`        | —                                                | Optional. Enables Apify scrapers     |
| `BANKROLL_USD`       | `1000`                                           | Capital base for Kelly sizing        |
| `MAX_KELLY_FRACTION` | `0.25`                                           | Cap on aggressive Kelly bets         |
| `MIN_EDGE`           | `0.02`                                           | Skip bets whose edge < 2 %           |
| `ASSETS`             | `BTC,ETH`                                        | Universe (comma separated)           |
| `LOOP_SECONDS`       | `60`                                             | Cycle cadence                        |
| `DASHBOARD_HOST`     | `127.0.0.1`                                      | FastAPI bind host                    |
| `DASHBOARD_PORT`     | `8765`                                           | FastAPI bind port                    |
| `PREDICTOR_DEFAULT`  | `auto`                                           | `auto` · `statistical` · `kronos` · `kronos-mini` / `small` / `base` · `ensemble` — used when the in-memory override is empty (after restart or `POST {"value":""}`) |

See **Kronos integration** and **Dashboard** for `KRONOS_*` variables and the live-desk dropdown (which does not require editing `.env` for day-to-day switches).

## Commands

| Command                     | Purpose                                                                |
| --------------------------- | ---------------------------------------------------------------------- |
| `python main.py once`       | Run a single cycle and exit (great for CI smoke tests).                |
| `python main.py demo`       | Run N cycles back-to-back so the feedback loop can grade itself.       |
| `python main.py loop`       | Production-style continuous loop (Ctrl-C to stop).                     |
| `python main.py status`     | Print recent hit-rate, total PnL, and number of stored cycles.         |
| `python main.py dashboard`  | Boot the FastAPI dashboard at `http://127.0.0.1:8765`.                 |
| `python main.py backtest`   | Walk-forward backtest on real OHLC, side-by-side per model. See below. |

## Live output (recorded)

The following is a verbatim capture from `python main.py demo --n 2 --sleep 3`
running on Windows / Python 3.13 / DEMO mode (no API keys, Binance OHLC):

```text
╭─────────────────────── CRYPTO ADS · AGENTS ────────────────────────╮
│   Model    nousresearch/hermes-3-llama-3.1-405b:free               │
│   Assets   BTC, ETH                                                │
│   Bankroll $1,000.00   MaxKelly 25%   MinEdge 2.0%                 │
│   LLM      OFF (demo narration)    Apify   OFF (Binance fallback)  │
╰────────────────────────────────────────────────────────────────────╯
──────────────────── CYCLE 2026-04-19 16:59:40 UTC ─────────────────────
INFO  Searching ['BTC', 'ETH'] on Polymarket + Kalshi (horizon=5m)
WARN  Kalshi: no live BTC markets, using DEMO
WARN  Kalshi: no live ETH markets, using DEMO
WARN  Polymarket fetch failed (timed out) — DEMO fallback
INFO  · polymarket/BTC yes=0.46 vol=$27254 — Will BTC/USD be higher in 5 minutes? (DEMO)
INFO  · kalshi/BTC     yes=0.59 vol=$13903 — Will BTC close higher in 5 min? (DEMO)
INFO  · kalshi/ETH     yes=0.44 vol=$10705 — Will ETH close higher in 5 min? (DEMO)
INFO  · polymarket/ETH yes=0.54 vol=$ 6167 — Will ETH/USD be higher in 5 minutes? (DEMO)
INFO  Fetching 1000 1m bars for ['BTC', 'ETH']
INFO  · ETH ← 1000 bars from binance
INFO  · BTC ← 1000 bars from binance
INFO  · BTC @5m  → FLAT p_up=0.487 conf=1.00 (markov-ema-fallback)
INFO  · BTC @15m → FLAT p_up=0.487 conf=1.00 (markov-ema-fallback)
INFO  · ETH @5m  → FLAT p_up=0.479 conf=1.00 (markov-ema-fallback)
INFO  · ETH @15m → FLAT p_up=0.479 conf=1.00 (markov-ema-fallback)
INFO  · polymarket/BTC side=YES edge=+0.032 stake=$ 59.08 EV=$ +4.18
INFO  · kalshi/BTC     side=NO  edge=+0.104 stake=$175.63 EV=$+44.57
INFO  · kalshi/ETH     side=YES edge=+0.037 stake=$ 65.77 EV=$ +5.46
INFO  · polymarket/ETH side=NO  edge=+0.057 stake=$106.90 EV=$+13.20
INFO  reflection: hit_rate=0.0% (n=0) total_pnl=$+0.00. latest_decisions=4, actionable=4.

                                   Decisions
┏━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ Venue      ┃ Asset ┃ Side ┃   Edge ┃ Stake $ ┃   EV $ ┃ Notes                ┃
┡━━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ polymarket │ BTC   │ YES  │ +0.032 │   59.08 │  +4.18 │ true_p=0.487 ...     │
│ kalshi     │ BTC   │ NO   │ +0.104 │  175.63 │ +44.57 │ true_p=0.513 ...     │
│ kalshi     │ ETH   │ YES  │ +0.037 │   65.77 │  +5.46 │ true_p=0.479 ...     │
│ polymarket │ ETH   │ NO   │ +0.057 │  106.90 │ +13.20 │ true_p=0.521 ...     │
└────────────┴───────┴──────┴────────┴─────────┴────────┴──────────────────────┘
╭─────────────── Hermes feedback ────────────────╮
│ hit_rate=0.0% (n=0) total_pnl=$+0.00.          │
│ latest_decisions=4, actionable=4.              │
╰────────────────────────────────────────────────╯
Sleeping 3 s before next cycle…
──────────────────── CYCLE 2026-04-19 17:00:01 UTC ─────────────────────
... [search + data + predict + risk same as before] ...
INFO  · polymarket/BTC pred=FLAT real=DOWN pnl=$ -59.08
INFO  · kalshi/BTC     pred=FLAT real=DOWN pnl=$+253.78
INFO  · kalshi/ETH     pred=FLAT real=UP   pnl=$ +83.03
INFO  · polymarket/ETH pred=FLAT real=UP   pnl=$-106.90
INFO  reflection: hit_rate=50.0% (n=4) total_pnl=$+170.83.
                  latest_decisions=4, actionable=3.
```

What just happened, in plain English:
1. Cycle 1 found 4 markets across both venues, fetched 1 000 1-minute
   bars for BTC and ETH from Binance, generated 5- and 15-minute
   predictions, and Kelly-sized 4 actionable positions.
2. Cycle 2 *graded* cycle 1's decisions against the realised price moves
   (`pred=FLAT real=DOWN pnl=$-59.08`, etc.), wrote each result to
   `data/feedback.jsonl`, and fed the rolling stats
   (`hit_rate=50.0% total_pnl=$+170.83`) back to Hermes for a
   self-critique that becomes context for the next cycle.

## Dashboard

```bash
python main.py dashboard
# → http://127.0.0.1:8765
```

The dashboard is intentionally **editorial-brutalist** — Fraunces display
serif paired with JetBrains Mono, warm cream paper / deep ink / electric
amber accent, subtle paper grain, hard rules, no gradients. It surfaces:

- **Predictor (live-desk)**: a masthead **dropdown** to switch, without
  restarting, between **Auto** (always **attempts Kronos** first, then
  Markov+EMA if the neural stack is not installed or errors),
  **Statistical only**, **Kronos mini / small / base** (options disabled
  until the local install is detected), and **Ensemble** (averages
  Kronos + statistical when both are available; falls back to statistical
  with a clear tag if not). The **resolved engine** and the
  `PREDICTOR_DEFAULT` / override story appear in a sub-line under the
  control; hover the card for a full `reason` string. (Internal
  `effective` keys like `kronos` or `stat` are **mapped to real `<option>`
  values** in the API as `select_value` so the closed control is never
  blank in Chrome / Edge on Windows — use `PREDICTOR_DEFAULT=kronos` in
  `.env` and the UI still shows `kronos-<KRONOS_MODEL_SIZE>`.)
- other KPIs: **LLM** (OpenRouter / Hermes id), **Universe** (assets), bankroll,
  Max-Kelly, Min-Edge, **Hermes** and **Apify** live / demo or off
- the latest cycle's **position cards** with a **model chip** (Kronos vs
  statistical vs ensemble) plus horizon, P(up), and direction per
  prediction
- a track-record sidebar (hit rate, total P&L, graded trade count)
- Hermes' latest written reflection
- a live-wire **audit ticker** (structured: prediction, prediction_leg
  in ensemble mode, decision, `cycle_done`) — auto-refreshing every 7 s

**Runtime predictor API (no process restart).**

| Method & path         | Body / notes | Purpose |
| --------------------- | ------------ | ------- |
| `GET /api/predictor`  | —            | Current status, `options[]` for the menu, `select_value` for the UI, `reason`, etc. |
| `POST /api/predictor` | `{"value":"auto"}` … `""` clears override | In-memory override until restart; then `PREDICTOR_DEFAULT` applies again |

Static assets use **cache-busted** URLs (`?v=<mtime>`) so the browser
refetches `app.js` / `styles.css` / `backtest.js` when any file on disk
changes.

API endpoints exposed by `api.py` (full set):

| Method & path                       | Purpose                                                   |
| ----------------------------------- | --------------------------------------------------------- |
| `GET /`                             | Live-desk HTML                                            |
| `GET /backtest`                     | Backtest workbench HTML                                   |
| `GET /api/state`                    | JSON: stats + settings + **predictor** + latest cycle + audit tail |
| `GET /api/predictor`                | **Predictor status + options** (for the live switcher)     |
| `POST /api/predictor`               | **Set/clear in-memory predictor override**                |
| `GET /api/run`                      | Trigger a one-shot cycle synchronously (~5-15 s)          |
| `GET /api/predictors`               | List installed predictors (statistical / kronos-*)        |
| `POST /api/backtest/run`            | Start a backtest in the background (returns `job_id`)     |
| `GET /api/backtest/jobs/{job_id}`   | Poll progress / completion of a backtest job              |
| `GET /api/backtest/list`            | List saved backtest runs (summary cards)                  |
| `GET /api/backtest/{run_id}`        | Full payload (samples + equity curve + calibration bins)  |
| `DELETE /api/backtest/{run_id}`     | Delete a saved run                                        |

### Implementation audit

| Area | Files / behaviour |
| ---- | ----------------- |
| **Predictor core** | `tools/kronos_predictor.py` — `resolve_predictor_choice()`, runtime override (`set_runtime_override` / `get_runtime_override`), **ensemble** path (avg of Kronos + statistical; honest tags), explicit Kronos in backtest still no silent swap. |
| **Live agent** | `agents/prediction_agent.py` — in **ensemble** mode, audits `prediction_leg` for statistical + kronos so the ticker shows both legs, then the blended `prediction` event. |
| **API** | `api.py` — `_predictor_status()` + `_ui_select_value()` for a valid `<select>`, `GET`/`POST /api/predictor`, `predictor` + `select_value` in `GET /api/state`, `asset_v` for static cache-bust. |
| **Live desk UI** | `dashboard/templates/index.html` — Predictor `<select>`, `select_value` for `selected` option, Hermes/LLM/Apify labelling. `dashboard/static/app.js` — `renderPredictorKpi`, `setSelectToKnownOption`, `switchPredictor`, card model chips, structured audit lines. `dashboard/static/styles.css` — `.kpi-select` solid background, `color-scheme: light`, native appearance for Windows reliability. |
| **Backtest UI** | `backtest.html` + `backtest.js` — form `action` neutralised, `type="button"` run, `bt-error` banner, per-section chart error isolation; Chart.js + adapter only (removed broken date-fns CDN). |
| **Backtest engine** | `backtest/*` — real OHLC only, walk-forward, metrics, DM, JSON sanitization for strict JSON. |
| **Config** | `config.py` / `.env.example` — `PREDICTOR_DEFAULT`, Kronos env vars, `data/backtests/`. |

## Backtest workbench

`/backtest` is a dedicated tab that runs **walk-forward backtests on real
exchange data** and renders calibration, hit-rate, equity, and
Diebold-Mariano significance side by side for every model you tick.

It is the answer to *"prove the model works"*. No synthetic bars are ever
substituted -- if the public OHLC fetch fails the run is aborted with a
loud error rather than silently fabricating data, because a backtest run
on fake bars is worse than no backtest.

### What it does

1. **Pulls real bars.** Bitstamp `/api/v2/ohlc` first (paginated to any
   depth), with a Binance `/api/v3/klines` fallback. The data source is
   recorded on every saved run.
2. **Walks forward.** For each anchor index `i` from
   `warmup .. len(bars) - horizon` step `step_bars`, it slices a
   trailing window of `warmup_bars` and feeds it to every selected
   predictor. There is no look-ahead at any point.
3. **Reads realised outcomes.** The realised forward direction and
   return are read from bars `i + horizon_bars`.
4. **Constructs a counterfactual market.** Polymarket and Kalshi do not
   expose tick-level historical books on their public APIs, so we price
   each contract at a configurable anchor (default 0.50 with a 4-vol
   noise term, which matches what these short-horizon BTC/ETH binary
   markets empirically trade around). The same Kelly + min-edge filter
   the live desk uses then sizes a hypothetical position.
5. **Computes per-model metrics.**
   - **Hit rate** (raw and confidence-banded)
   - **Brier score** and **log-loss** (calibration)
   - **Equity curve**, **total P&L**, **Sharpe**, **max drawdown**
6. **Diebold-Mariano significance.** Every pair of models is compared on
   per-prediction Brier loss with a two-sided Newey-West-corrected DM
   test. The dashboard prints the verdict in plain English
   (*"kronos-small beats statistical on Brier loss (significant,
   p=0.012)"*).
7. **Persists.** Every run lands as a single JSON file under
   `data/backtests/<id>.json` and is reachable forever from the
   workbench's history list.

### Use it from the dashboard

```bash
python main.py dashboard
# Open http://127.0.0.1:8765/backtest
```

Pick the asset, bar interval, horizon, history depth, warmup window,
step, and the models you want to compare. Press *Run backtest*. A live
progress bar tracks the job; when it finishes the report appears with:

- a side-by-side **scoreboard table** with the best model marked `*`
- the **equity curves** for each model on the same time axis
- a **reliability diagram** (predicted P(up) vs realised frequency)
- a **forecast track** showing each prediction with a green dot when the
  call was right
- the **Diebold-Mariano** verdict per pair

Past runs are listed on the left so you can come back to any of them
later or delete them.

### Use it from the CLI

```bash
python main.py backtest --asset BTC --interval 5m --horizon-minutes 5 \
  --days 14 --warmup 200 --step 1 --models statistical,kronos-small
```

The CLI prints the same scoreboard table to the terminal and saves the
run under `data/backtests/`. Statistical-only is always available; the
Kronos rows light up after you complete the install in the next
section. **If you tick a Kronos model that isn't installed, the
backtester records a per-anchor failure for that model rather than
silently substituting the statistical baseline** -- that way the
side-by-side comparison stays honest.

### Verified end-to-end run

Recorded from this codebase (Windows / Python 3.13, Binance fell back
in for Bitstamp during a transient connection drop):

```text
backtest.data: bitstamp fetch failed ([WinError 10054] ...) -- trying binance
backtest.data: binance returned 576 bars for BTC @ 5m
backtest.engine: data_source=binance bars=576 interval=5m horizon_bars=1
backtest.engine: completed eca484e73c68 in 1.8s (63 anchors x 1 models)

                Backtest eca484e73c68 -- BTC 5m (horizon 5m)
Model     N    Hit    Conf-Hit   Brier    LogLoss   PnL $     Sharpe   MaxDD %
statist.. 63   0.460  nan (0)    0.2546   0.7024   -209.09   -45.86   -29.50

Saved to data/backtests/eca484e73c68.json -- view in /backtest
```

The negative P&L is the **point**: the backtester has no incentive to
make any model look good, and on this 2-day BTC slice the statistical
baseline lost money. That's exactly the kind of objective evidence the
workbench is built to surface.

## Kronos integration (local install)

Kronos is **not hosted as a managed inference service** -- the official
[NeoQuasar/Kronos](https://github.com/NeoQuasar/Kronos) project ships
weights on Hugging Face but you have to run the model yourself. The
codebase is wired so that, once you complete the local install, the
dashboard's predictor picker lights up and the backtester compares
Kronos against the statistical baseline on real bars.

### Step 1 -- install PyTorch + Hugging Face

```bash
# CPU-only is fine for kronos-mini and kronos-small.
# For a GPU, follow https://pytorch.org/get-started/locally/ first.
pip install torch>=2.2.0 transformers>=4.40.0 huggingface-hub>=0.23.0 einops>=0.7.0
```

### Step 2 -- clone the official Kronos repo

```bash
git clone https://github.com/NeoQuasar/Kronos.git
pip install -r Kronos/requirements.txt
```

### Step 3 -- point the project at the clone

In your `.env`:

```env
KRONOS_REPO_PATH=C:\absolute\path\to\Kronos    # PowerShell-friendly
KRONOS_MODEL_SIZE=small                          # mini | small | base
KRONOS_TOKENIZER=NeoQuasar/Kronos-Tokenizer-base
KRONOS_DEVICE=auto                               # auto | cpu | cuda | cuda:0
KRONOS_MAX_CONTEXT=512
```

`KRONOS_MODEL_PATH` is optional -- set it to a local directory to use
weights you've already downloaded. Otherwise the project will pull from
Hugging Face the first time it runs (`NeoQuasar/Kronos-<size>`).

### Available sizes

| Size  | Repo                          | Params  | Best for                                   |
| ----- | ----------------------------- | ------- | ------------------------------------------ |
| mini  | `NeoQuasar/Kronos-mini`       | 4.1 M   | Laptop CPU, fastest sanity check           |
| small | `NeoQuasar/Kronos-small`      | 24.7 M  | Default. Solid CPU latency, GPU-friendly   |
| base  | `NeoQuasar/Kronos-base`       | 102.3 M | Best forecasts, recommend GPU              |

The dashboard's *Backtest* tab and the CLI both expose all three under
`kronos-mini` / `kronos-small` / `kronos-base`. Tick whichever you
installed; the rest stay greyed out as `not installed`.

### How it loads

`tools/kronos_predictor.py` tries two import strategies, in order:

1. `from kronos import Kronos, KronosTokenizer, KronosPredictor` (in
   case a fork has published a pip-style package).
2. Prepend `KRONOS_REPO_PATH` to `sys.path` and
   `from model import Kronos, KronosTokenizer, KronosPredictor` (the
   canonical layout of the official NeoQuasar repo).

The loaded `(predictor, model_id)` tuple is cached per `(size, device)`
so a backtest with hundreds of anchors does not re-download or
re-instantiate the model.

### How predictions are formed

Inside the predictor we:

1. Convert our `PriceSeries` to the OHLCV+amount pandas DataFrame the
   official `KronosPredictor.predict()` expects.
2. Build `x_timestamp` from the input window and `y_timestamp` for the
   forecast horizon (`pred_len` is computed from the bar size and the
   requested horizon in minutes).
3. Sample once with `T=1.0`, `top_p=0.9` (overridable via env) and read
   the predicted close at `y_timestamp[-1]`.
4. Squash the predicted % change through a logistic into `prob_up`,
   then map to `UP / DOWN / FLAT`. The same `Prediction` shape comes
   back out, so the orchestrator and the backtester are agnostic to
   which model produced it.

### Sanity-checking the install

```bash
python -c "from tools.kronos_predictor import list_available_predictors; \
import json; print(json.dumps(list_available_predictors(), indent=2))"
```

Every Kronos row in the output should now show `"available": true`.

## Architecture deep-dive

### `core/llm.py` — Hermes-style tool-calling loop

The upstream `nousresearch/hermes-agent` is not published as a pip
package. `core/llm.py` implements the documented Hermes
function-calling pattern (system prompt + JSON tool schema + iterative
tool-use loop) targeting Hermes models hosted on **OpenRouter**:

```python
def run_hermes_loop(system_prompt, user_prompt, tools, *, max_iters=5):
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt}]
    for _ in range(max_iters):
        resp = chat(messages, tools=[t.to_openai_schema() for t in tools])
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content
        # ... execute each tool, append role="tool" turns, loop ...
```

Because OpenRouter is OpenAI-API-compatible, the loop works with **any**
tool-using model — swap `OPENROUTER_MODEL` to `openai/gpt-4o-mini`,
`anthropic/claude-3.5-sonnet`, etc. without touching code.

### `tools/polymarket.py` & `tools/kalshi.py`

Both venues are queried in parallel by the `MarketSearchAgent`. Each
adapter:

1. Hits the public read-only endpoint
   (`gamma-api.polymarket.com/markets`,
   `api.elections.kalshi.com/trade-api/v2/markets`).
2. Filters to crypto markets matching the asset keyword whose close
   window is `[1 min, horizon × 4)`.
3. Normalises the result into the shared `Market` Pydantic type.
4. On any failure (timeout, rate limit, region block) emits a realistic
   synthetic market so the rest of the pipeline keeps running.

### `tools/apify_scraper.py`

Order of preference: **Apify → Binance public REST → synthetic random
walk**.

The Apify path runs `apify/cheerio-scraper` (configurable via
`APIFY_POLYMARKET_ACTOR`) against **Bitstamp**'s public OHLC endpoint
and ships a custom `pageFunction` that extracts up to 1 000 1-minute
bars in a single request. Bitstamp is used instead of Binance because
**Apify Proxy IPs are blocked by Binance with HTTP 451** ("Service
unavailable from a restricted location"); Bitstamp accepts them
globally. The actor returns a Bitstamp `data.ohlc` array which is
normalised into the shared `OHLCBar` shape.

The Binance fallback uses the unauthenticated `/api/v3/klines` endpoint
**from the local IP** (no key required, no geo-block) so the default
install never goes "offline" even if the Apify quota is exhausted.

### `tools/kronos_predictor.py`

If `torch` and the upstream `kronos` package
(<https://github.com/shiyu-coder/Kronos>) are installed, the predictor
uses Kronos' K-line foundation model. Otherwise it falls back to a
**Markov + EMA blended predictor** inspired by the brief's
*"Cracking the Morning Code"* article:

- compute first-order Markov transition probabilities `P(up | prev_up)`
  and `P(up | prev_down)` with Laplace smoothing
- compute fast-vs-slow EMA momentum and squash with a logistic
- blend `0.6 · Markov + 0.4 · momentum`
- scale confidence by sample size and inverse realised volatility

Both paths return the same `Prediction` shape, so swapping is invisible
to the rest of the system.

### `tools/kelly.py`

Standard Kelly fraction for a binary contract priced at `q` with model
probability `p`:

```
b  = (1 - q) / q                  # net odds
f* = (b · p − (1 − p)) / b
   = (p − q) / (1 − q)            # for the YES side
```

We compute the Kelly fraction for both YES and NO, pick the side with
the larger positive edge, multiply by the model's self-confidence,
clamp to `MAX_KELLY_FRACTION`, and skip anything below `MIN_EDGE`. EV
is the closed-form expected dollar value of the resulting bet.

A small `arbitrage_score()` helper compares the average 5-min `prob_up`
to the 15-min view; disagreements ≥ 5 pp are emitted as `arbitrage`
audit events.

### `agents/feedback_agent.py`

After each cycle the feedback agent:

1. Walks the *prior* cycle's decisions and computes the realised return
   between the bar at the time the decision was taken and the latest
   bar.
2. Marks each decision as correct/incorrect, computes contract-style
   PnL (`stake · (1 − price) / price` on win, `−stake` on loss), and
   appends a `FeedbackRecord` to `data/feedback.jsonl`.
3. Calls Hermes (when an `OPENROUTER_API_KEY` is set) with rolling
   stats and asks for a 2–3 sentence critique. The critique becomes
   context for the next cycle's narrators.

## Scaling levers

The code already supports — without modification — every one of the
"think outside the box" suggestions in the brief:

- **More assets.** Set `ASSETS=BTC,ETH,SOL,DOGE,…`. The data-fetch
  agent is `ThreadPoolExecutor`-pooled and every downstream stage is
  asset-agnostic.
- **Internal arbitrage (5 m vs 15 m).** Built-in. The `RiskAgent`
  scans multi-horizon predictions and emits structured `arbitrage`
  audit events when short- and long-horizon views disagree by ≥ 5 pp.
- **More venues.** Add a new module under `tools/` that returns a list
  of `Market`. Wire it into `MarketSearchAgent.run` (10 lines).
- **User visibility.** The FastAPI dashboard streams the live audit
  log, the latest decisions, the rolling hit-rate / PnL, and Hermes'
  self-critique.
- **Pluggable LLM.** Any OpenRouter-supported tool-use model works —
  Hermes 3, GPT-4o-mini, Claude 3.5 Sonnet, etc. Swap one env var.
- **Pluggable predictor.** Replace the Markov-EMA fallback with a
  drop-in module that returns a `Prediction`. Kronos is wired up if
  installed.
- **Resilience.** Every external call is wrapped in `try/except` with
  a realistic fallback — the loop never dies because of a flaky
  third-party.

## Logging, persistence, observability

| Artefact              | Path                       | Format                                  |
| --------------------- | -------------------------- | --------------------------------------- |
| Console               | stdout                     | Rich (truecolor, no legacy Windows path)|
| Audit trail           | `logs/audit.jsonl`         | One JSON object per event               |
| Per-cycle snapshots   | `data/cycles.jsonl`        | One full `CycleResult` per line         |
| Realised P&L feedback | `data/feedback.jsonl`      | One `FeedbackRecord` per resolved trade |

Sample audit events:

```json
{"ts": 1776617800.43, "event": "search_done", "n_markets": 4, "polymarket": 2, "kalshi": 2}
{"ts": 1776617820.11, "event": "prediction",  "asset": "BTC", "horizon": 5, "direction": "FLAT", "prob_up": 0.487, "model": "markov-ema-fallback"}
{"ts": 1776617820.18, "event": "risk_decision","venue": "kalshi", "asset": "BTC", "side": "NO", "edge": 0.104, "stake_usd": 175.63, "ev_usd": 44.57}
{"ts": 1776618019.64, "event": "feedback",     "venue": "kalshi", "asset": "BTC", "correct": true, "pnl_usd": 253.78}
{"ts": 1776618019.65, "event": "cycle_done",   "duration_s": 18.3, "n_markets": 4, "n_decisions": 4, "n_errors": 0}
```

**Additional structured events (predictor & ensemble):**

| `event`                 | When | Notable fields |
| ----------------------- | ---- | -------------- |
| `prediction_leg`        | Ensemble mode: each engine run separately for the audit ticker | `asset`, `horizon`, `direction`, `prob_up`, `model` (e.g. `markov-ema-fallback` or `kronos-small`) |
| `prediction_leg_error`  | Ensemble mode: explicit `kronos` leg throws (import / OOM / etc.) | `asset`, `horizon`, `leg`, `error` (truncated) |

The dashboard's audit ticker tails this file so you can watch the
agents work in real time without leaving the browser.

### Predictor and Kronos console log reference

All of the following go to the **Rich** console (stdout) from logger
`cwt.tool.predict` in `tools/kronos_predictor.py` unless noted. This is
what you are reading when you run `python main.py dashboard` and trigger
`GET /api/run` or the loop. **Levels:** `INFO` and `WARNING` are visible
in the default configuration; the code does not hide Kronos failures in
`DEBUG` after the first bar (repeat failures are `INFO` with a short
reason).

| Level | When it appears | Message pattern (abridged) | Meaning |
| ----- | --------------- | -------------------------- | ------- |
| `INFO` | Once per process, only if the desk / `PREDICTOR_DEFAULT` is **statistical** (or `stat` / `baseline`) and the call is the top-level predictor (not an inner `model=` audit leg) | `Predictor mode is statistical / baseline only — Kronos is **not** called …` | You asked for the Markov+EMA path only. No Kronos import is attempted. No Kronos "error" is expected. |
| `WARNING` | First time in a process that **Auto** (default "prefer Kronos" path) calls `_kronos_predict` and it **throws** (import, HF, OOM, etc.) | `Kronos first-attempt failed (%s) — using Markov+EMA. Next failures each cycle: INFO with import/runtime reason. Fix: install torch, clone … NeoQuasar/Kronos, set KRONOS_REPO_PATH.` | The full exception is included in the log after the first `%s`. Explains that later horizons in the same run use shorter `INFO` lines. |
| `INFO` | Every further **Auto** prediction in that process where Kronos still fails (typically once per asset × per horizon, e.g. 4 lines per cycle for BTC+ETH @ 5m+15m) | `auto: Kronos did not run → {ExceptionType}: {first ~200 chars of message} (statistical fallback for this bar)` | Confirms the bar used Markov+EMA because Kronos did not return a prediction. |
| `WARNING` | First **ensemble** Kronos leg failure in a process | `ensemble: Kronos leg failed (%s) — blend uses statistical only until fixed.` | Full exception on first line; blend is tagged `ensemble:statistical-only` in `model_name` for that prediction. |
| `INFO` | Further ensemble Kronos leg failures in the same process | `ensemble: Kronos leg failed again → {type}: {message}` | Same situation as the row above; shorter line. |
| `INFO` | **Only if Kronos actually loads** (first time each process loads weights for a given size/device cache key) | `Kronos: loading tokenizer=… model=… device=… max_context=…` | Weights are being pulled from Hugging Face or a local `KRONOS_MODEL_PATH`. Not printed when Kronos never imports. |

**Prediction agent line** (logger `cwt.orch` child `cwt.agent.predict` — `agents/prediction_agent.py`):

| Level | Pattern | Meaning |
| ----- | ------- | ------- |
| `INFO` | `[agent.predict]· {ASSET} @5m|15m → {DIR} p_up=… conf=… ({model_name})` | One line per (asset, horizon). The `model_name` in parentheses is the source of truth: e.g. `kronos-small`, `markov-ema-fallback` (after a failed Kronos attempt in Auto), `ensemble:kronos-small+markov-ema-fallback` (blended), `ensemble:statistical-only` (Kronos leg failed in ensemble), `insufficient-data` (fewer than 30 bars). |

**Typical order** when Kronos is **not** installed and the desk is on **Auto** (per cycle, after OHLC is fetched):

1. One `WARNING` — `Kronos first-attempt failed (Could not import Kronos. … ModuleNotFoundError…)` (first bar only in that server process).
2. For each remaining horizon: `INFO` — `· BTC @5m → … (markov-ema-fallback)` then `INFO` — `auto: Kronos did not run → ImportError: … (statistical fallback for this bar)` (or equivalent exception type).
3. Risk / feedback lines from other agents (unchanged).

**Typical order** when Kronos **is** installed and loads successfully:

1. On first use of a (size, device) in cache: `INFO` — `Kronos: loading tokenizer=…`.
2. `[agent.predict]· … (kronos-small)` (or the active size) without any `Kronos first-attempt failed` warning.

**Uvicorn / FastAPI** lines (`INFO: 127.0.0.1 - "GET /api/…"`) are HTTP access logs from the dashboard polling `/api/state` and are not predictor events.

## Testing & smoke verification

The minimum viable verification:

```bash
python main.py demo --n 2 --sleep 3
```

Expected after ~30 s:

- A styled banner.
- Two cycles' worth of `INFO` lines.
- The second cycle prints feedback like
  `pred=FLAT real=DOWN pnl=$-59.08` and a non-zero hit-rate /
  total-PnL line.
- New files appear under `data/` and `logs/`.

To verify the dashboard:

```bash
python main.py dashboard
# Open http://127.0.0.1:8765 in any browser.
```

You can also exercise it programmatically:

```python
from fastapi.testclient import TestClient
from api import app
c = TestClient(app)
assert c.get("/").status_code == 200
print(c.get("/api/state").json()["stats"])
```

Recorded test output (this codebase, Windows / Python 3.13, no API
keys):

```text
GET /            -> 200  3 837 bytes
GET /api/state   -> 200  keys: ['stats', 'settings', 'latest_cycle', 'audit_tail']
                          stats: {'n_feedback': 4, 'hit_rate': 0.5, 'total_pnl_usd': 170.83}
                          latest_cycle present: True
                          audit_tail len: 52
```

## Roadmap

- [ ] Wire a venue-execution adapter so Kelly-sized decisions can flow
      to a paper-trading endpoint (currently the desk only **records**
      decisions — it does not auto-execute).
- [ ] Bring in real Kronos weights via Hugging Face (uncomment the
      `torch` / `transformers` lines in `requirements.txt`).
- [ ] Add a Bayesian online-learning layer on top of the Markov chain
      to reweight transition probabilities by recent regime.
- [ ] Persist cycles to SQLite for richer dashboard analytics.
- [ ] Add a Telegram / Discord notifier on `arbitrage` audit events.

## FAQ & honest caveats

**Q. Why doesn't this place real trades?**
By design. The brief asked for a research and sizing system. Wiring
to a venue's order endpoint is a one-file addition and intentionally
left to the operator.

**Q. Hermes-3-405B free is slow / rate-limited.**
Yes. Set `OPENROUTER_MODEL` to a faster free model (e.g.
`meta-llama/llama-3.1-8b-instruct:free`) for higher throughput. The
agents fall back to deterministic narration if the LLM call fails.

**Q. Polymarket / Kalshi keep returning "DEMO".**
Polymarket geo-blocks several regions and Kalshi requires an account
for some endpoints. The DEMO fallback is realistic and lets you
verify every other stage of the pipeline without touching the
venues.

**Q. Why does Apify route through Bitstamp instead of Binance?**
Binance returns HTTP 451 ("Service unavailable from a restricted
location") to every Apify Proxy IP, so an Apify-fronted Binance call
yields a JSON error body, not OHLC. Bitstamp's public OHLC endpoint
accepts Apify Proxy from anywhere and serves up to 1 000 bars in a
single response, which matches the project's default `KLINE_LIMIT`.
The local-IP Binance fallback is preserved for environments where
Apify isn't configured.

**Q. Does this run on macOS / Linux?**
Yes. The Windows-specific UTF-8 stream reconfiguration in
`core/logger.py` is gated by `sys.platform == "win32"`.

## Credits & License

Built by **[Danish Akhtar](https://danishakhtar.tech)** —
[github.com/danish296](https://github.com/danish296).

Acknowledgements:
- [Nous Research · Hermes](https://nousresearch.com/) for the
  function-calling pattern this loop is modelled on.
- [OpenRouter](https://openrouter.ai/) for the unified LLM API and the
  free Hermes-3 endpoint.
- [Apify](https://apify.com/) for the actor model.
- [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) for the
  K-line foundation model.
- [*"Cracking the Morning Code"*](https://medium.com/@wl8380/cracking-the-morning-code-predicting-market-opens-with-markov-chains-558fe419df43)
  — the Markov-chain morning-open article that inspired the fallback
  predictor.
- The Polymarket / Kalshi Kelly-sizing references in the project
  brief.

Released under the [MIT License](https://opensource.org/licenses/MIT).

---

<div align="center">

**[danishakhtar.tech](https://danishakhtar.tech)** · made with intention
on Apr 2026

</div>
