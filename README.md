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
8. [Dashboard](#dashboard)
9. [Architecture deep-dive](#architecture-deep-dive)
10. [Scaling levers](#scaling-levers)
11. [Logging, persistence, observability](#logging-persistence-observability)
12. [Testing & smoke verification](#testing--smoke-verification)
13. [Roadmap](#roadmap)
14. [FAQ & honest caveats](#faq--honest-caveats)
15. [Credits & License](#credits--license)

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
└── dashboard/
    ├── templates/index.html              ← editorial-brutalist HTML
    └── static/{styles.css, app.js}       ← Fraunces + JetBrains Mono · amber / ink / cream
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

## Commands

| Command                     | Purpose                                                                |
| --------------------------- | ---------------------------------------------------------------------- |
| `python main.py once`       | Run a single cycle and exit (great for CI smoke tests).                |
| `python main.py demo`       | Run N cycles back-to-back so the feedback loop can grade itself.       |
| `python main.py loop`       | Production-style continuous loop (Ctrl-C to stop).                     |
| `python main.py status`     | Print recent hit-rate, total PnL, and number of stored cycles.         |
| `python main.py dashboard`  | Boot the FastAPI dashboard at `http://127.0.0.1:8765`.                 |

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

- live KPIs (model, universe, bankroll, Max-Kelly, Min-Edge, LLM/Apify
  status)
- the latest cycle's positions as oversized cards (venue, side, edge,
  stake, EV, notes)
- a track-record sidebar (hit rate, total PnL, graded trade count)
- Hermes' latest written reflection
- a live-wire **audit ticker** auto-refreshing every 7 s

API endpoints exposed by `api.py`:

| Method & path     | Purpose                                                   |
| ----------------- | --------------------------------------------------------- |
| `GET /`           | The dashboard HTML                                        |
| `GET /api/state`  | JSON: stats + settings + latest cycle + audit tail        |
| `GET /api/run`    | Trigger a one-shot cycle synchronously (~5–15 s)          |

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
walk**. The Apify path is generic — it accepts a configurable actor ID
(`APIFY_POLYMARKET_ACTOR`) and either ingests a structured dataset or
parses raw Binance-style klines. The Binance fallback uses the
unauthenticated `/api/v3/klines` endpoint (no key required) so the
default install never goes "offline".

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

The dashboard's audit ticker tails this file so you can watch the
agents work in real time without leaving the browser.

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

**Q. Why not use Binance via Apify?**
You can — set `APIFY_TOKEN` and `APIFY_POLYMARKET_ACTOR` to a custom
actor that scrapes Binance and returns a dataset. The `_items_to_bars`
helper accepts both shaped dicts and raw kline arrays.

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
