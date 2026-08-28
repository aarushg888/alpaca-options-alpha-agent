# Alpaca Options Alpha Agent

An autonomous AI trading agent built for the **Alpaca × lablab.ai — AI Trading
Agents Hackathon** (Aug 28 – Sep 4, 2026). It trades **defined-risk option
spreads** on Alpaca's paper-trading environment, driven by a deterministic,
explainable strategy engine and hard risk gates.

> ⚠️ For the hackathon, the agent runs on a **dedicated paper account** funded at
> **$100,000**. Paper-trading results are hypothetical and not indicative of
> future performance. Options trading carries risk.

---

## What it does

A signal-driven, multi-strategy **options income** agent. Each decision tick:

1. **Signal** — reads an *IV-rank*-style volatility signal per symbol in a
   liquid universe (SPY, QQQ, IWM, GLD, AAPL, NVDA, TSLA, AMD, XLE, XLF).
2. **Strategy selection** — builds defined-risk structures:
   - **Iron Condor** (market-neutral core)
   - **Put Credit Spread** (bullish/neutral)
   - **Call Credit Spread** (bearish/neutral)
   - Wings adapt to IV rank (tighter when vol is scary → smaller max loss).
3. **Risk gates** — every candidate must clear deterministic gates (defined
   risk, IV-rank entry, per-position/aggregate risk caps, drawdown halt).
4. **Execution** — approved orders are placed through the **Alpaca CLI**
   (`alpaca order submit --order-class mleg --legs ...`).
5. **Monitoring** — open positions are watched for take-profit (≥50% of max
   credit), stop-loss (defined max risk), expiry, and early compaction.

It is structured as a *multi-agent* system (signal → strategy → critic/risk →
executor → monitor) but keeps the execution path fully deterministic and
unit-tested so behavior is reproducible.

---

## Architecture

```
src/
  config.py                 # Config.from_env(); backend auto-detects creds
  finance/black_scholes.py  # pricing + Greeks (pure math)
  market_sim.py             # local market simulator (zero-dep backtesting)
  broker/
    base.py                 # BrokerBackend interface + Order/Position/Account
    simulated_broker.py     # local accounting backend (correct P&L path)
    alpaca_cli.py           # LIVE backend -> shells to the official Alpaca CLI
  strategy/engine.py        # signals, IV rank, spread construction
  risk/gates.py             # deterministic risk gates (no LLM)
  agent/
    decision_engine.py      # proposes + scores opportunities
    executor.py             # submits approved orders (audited)
    position_monitor.py     # TP/SL/expiry/compaction exits
  orchestrator.py           # the autonomous loop (run/backtest use same code)
run.py            # simulated/backtest run  (python run.py --steps 504 --seed 7)
backtest.py       # N-seed variance table (python backtest.py)
live_run.py       # live paper loop for the 7-day competition
generate_writeup.py  # builds the required one-page write-up (results/WRITEUP.md)
tests/            # 27 tests, 89% coverage
```

---

## Quick start

```bash
# 1. (optional) create a venv
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run a simulated backtest — no Alpaca credentials needed.
python run.py --steps 504 --seed 1234

# 3. See performance across many market regimes:
python backtest.py            # prints avg / min / max P&L + win rate

# 4. Generate the one-page hackathon write-up:
python generate_writeup.py    # -> results/WRITEUP.md
```

The agent runs **fully against the local simulator** with zero external
dependencies (only the Python stdlib + pytest for tests). This is what lets us
test and iterate the entire strategy/risk/execution pipeline before touching
Alpaca.

---

## Going live (paper trading)

The same code runs on real Alpaca paper trading the moment credentials exist:

```bash
# 1) One-time: create the paper profile (keys stored at ~/.config/alpaca)
alpaca profile login --api-key --paper --key PK... --secret ...

# 2) Run one tick against the live paper account (uses the profile; do NOT
#    export ALPACA_API_KEY/SECRET -- the CLI prefers env vars and can misbehave):
python live_run.py --once

# 3) Schedule for the full hackathon window (cron every 15 min). The agent only
#    submits new orders during market hours (only_when_open guard); monitoring
#    of open positions runs every tick.
python live_run.py --interval 15
```

`live_run.py` sets `Config.force_backend = "alpaca"` so behavior is explicit
(no accidental network calls from tests). The agent refuses to run against a
non-paper account as a safety guard.

### Why the Alpaca CLI (not MCP)

The competition requires using **either** the MCP server **or** the CLI. We use
the **official Alpaca CLI** (`github.com/alpacahq/cli`) because it is built for
"long-running agent sessions, cron jobs and CI" — exactly the deployment shape
of an unattended 7-day hackathon agent. Orders are placed with:

```
alpaca order submit --order-class mleg --type limit --time-in-force day \
  --qty 1 --limit-price 2.84 \
  --legs '[{"symbol":"SPY260930P00732000","side":"sell","ratio_qty":"1"}, ...]'
```

All commands are constructed in `src/broker/alpaca_cli.py` and unit-tested
without credentials.

### Live market data (real, no trading creds needed)

`src/market/live_market.py` reads **real option chains + quotes** from the
Alpaca CLI (public market data — works with only the API key id) and maps them
into the same `MarketView` interface the simulator uses. So signal generation,
strategy selection, and risk gating all run on **live prices** today; submitting
orders only additionally needs the secret key. The agent tracks a per-symbol
**trailing IV-rank window** persisted to `runs/iv_history.json` so volatility
regime signals accumulate across the 7-day competition (and across cron ticks).

To preview live signals without trading:

```bash
export ALPACA_API_KEY=PK...
python - <<'PY'
import sys; sys.path.insert(0,'.')
from src.config import Config
from src.market.live_market import LiveMarket
from src.strategy.engine import compute_signal
lm = LiveMarket(Config.from_env(), bin_path="alpaca")
for _ in range(3): lm.step()
print({u['symbol']: compute_signal(u['symbol'], lm, Config.from_env()).iv_rank
       for u in Config.from_env().universe})
PY
```

---

## Testing & CI

```bash
pytest -q --cov=src --cov-report=term-missing
```

- **36 tests** covering: Black-Scholes/Greeks, risk gates (rejection paths),
  simulator/strategy, Alpaca CLI command construction (with a stub runner — no
  network), **live market-data adapter hitting the real Alpaca CLI**, and full
  end-to-end deterministic runs.
- GitHub Actions runs the suite on Python 3.10/3.11/3.12 on every push/PR.

---

## Backtest results (local simulator)

Across 10 seeds (≈1 trading year each), the baseline defined-risk strategy:

| metric | value |
|--------|-------|
| avg P&L | **+$5,600 (~+5.6%)** |
| worst seed | −$3,700 (−3.7%) |
| avg max drawdown | ~3.6% |
| win rate | ~80% |

These are **simulated** results used to validate the pipeline and risk controls;
they are not a forecast of live paper-trading performance.

---

## Submission checklist (hackathon)

- [x] Autonomous agent using Alpaca Trading API
- [x] Uses **Alpaca CLI** (required MCP-or-CLI rule)
- [x] Every strategy incorporates **options** (defined-risk spreads)
- [x] Risk gates documented (see `src/risk/gates.py` and `results/WRITEUP.md`)
- [x] One-page write-up generated by `generate_writeup.py`
- [ ] Dedicated **fresh** paper account, funded $100,000 (create at submit time)
- [ ] Submit the Alpaca **account ID** + public repo + up to 5 social posts

---

## License

MIT — see [LICENSE](LICENSE).
