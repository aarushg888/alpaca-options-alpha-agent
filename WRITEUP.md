# Alpaca Options Alpha Agent — Hackathon Write-Up

**Event:** Alpaca × lablab.ai — AI Trading Agents Hackathon (Aug 28 – Sep 4, 2026)
**Submission type:** Autonomous AI trading agent (Options Alpha)
**Execution backend:** Alpaca Trading API via the official **Alpaca CLI** (`github.com/alpacahq/cli`)
**Account:** dedicated paper trading account, starting balance $100,000

---

## 1. AI Logic (how the agent thinks)

The agent is a **signal-driven, multi-strategy options income engine** built on
defined-risk spreads. Each decision tick runs a deterministic, explainable
pipeline (no black-box LLM in the execution path):

1. **Regime / volatility signal.** For every symbol in a liquid ETF + single-stock
   universe (SPY, QQQ, IWM, GLD, AAPL, NVDA, TSLA, AMD, XLE, XLF) the agent reads
   an *IV rank*-style volatility signal. Higher IV rank ⇒ richer premium ⇒ more
   attractive to sell.
2. **Strategy selection.** Based on the signal the agent builds candidate
   **defined-risk** structures:
   - **Iron Condor** (market-neutral core) — sell OTM put spread + sell OTM call spread.
   - **Put Credit Spread** (bullish/neutral) — sell OTM put, buy lower put.
   - **Call Credit Spread** (bearish/neutral) — sell OTM call, buy higher call.
3. **Scoring.** Opportunities are ranked by signal strength then net premium.
4. **Critic pass (risk gates).** Every candidate must pass deterministic risk
   gates (below) before it is submitted.
5. **Execution + monitoring.** Approved orders are placed through the Alpaca CLI;
   open positions are watched every bar for **take-profit (≥50% of max credit)**,
   **stop-loss (defined max risk)**, **expiry settlement**, and **early
   compaction** to recycle risk budget.

The design mirrors a *multi-agent* system (specialized signal agent → strategy
agent → critic/risk agent → executor agent → monitor agent) but keeps the
execution path fully deterministic and unit-tested so behavior is reproducible.

## 2. Risk Gates (deterministic, no LLM in the loop)

All orders must clear **every** gate; otherwise the trade is rejected:

| Gate | Rule |
|------|------|
| Defined risk | Only net-credit spreads; max loss = spread width − net credit (known at entry). |
| IV-rank entry | Enter only when IV rank ≥ 35. |
| Per-position risk | Max loss ≤ 4% of equity ⇒ caps position size. |
| Capital ceiling | Net premium per position ≤ 6% of equity. |
| Aggregate risk | Total defined risk across book ≤ 20% of equity. |
| Position count | At most 8 concurrent positions; target 5. |
| Drawdown halt | New entries freeze if total drawdown ≥ 15%. |
| DTE window | Only 21–60 day expiries (income, not lottery). |

Every submission is logged with its risk rationale for full auditability.

## 3. Alpaca Infrastructure Implementation

- **Trading API / CLI.** Orders are placed with the official Alpaca CLI:
  `alpaca order submit --order-class mleg --legs '<json>'` for multi-leg option
  spreads, `alpaca position close` to exit, `alpaca account get` for balances,
  `alpaca option contracts` to discover chains. This satisfies the competition's
  *"utilize either Alpaca's MCP server or its CLI tools"* requirement.
- **Dedicated paper account.** Trades run on a fresh paper account funded at
  $100,000 (paper environment — no real capital at risk).
- **Options trading.** Every strategy is an options strategy; the book is built
  entirely from defined-risk option spreads, satisfying the *"all strategies
  must incorporate options trading"* rule.
- **Pluggable backend.** The same agent code runs against a local market
  simulator (for testing/backtests with zero dependencies) and flips to the live
  Alpaca CLI when credentials are present — `Config.backend` auto-detects.
- **Autonomy.** `live_run.py` is intended to run on a cron (every
  15 min) for the full 7-day window, fully
  unattended.

## 4. Performance (from latest run)

- Backend: **simulated**
- Final equity: **$107,942.12**
- Total P&L: **$7,942.12** (7.94%)
- Trades placed: **124**
- Max drawdown: **2.68%**

*Generated 2026-08-27 00:24 UTC. Paper-trading
results are hypothetical and not indicative of future performance.*
