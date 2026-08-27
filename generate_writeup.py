#!/usr/bin/env python3
"""Generate the required one-page hackathon write-up (AI logic, risk gates,
and Alpaca infrastructure implementation) plus a performance summary from the
latest run. Output: WRITEUP.md (and writes results/summary.md).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402


def generate(run_json_path: Path | None = None) -> str:
    cfg = Config.from_env()
    run_data = None
    if run_json_path and run_json_path.exists():
        try:
            run_data = json.loads(run_json_path.read_text())
        except Exception:
            run_data = None

    summary = (run_data or {}).get("summary", {}) if run_data else {}
    backend = summary.get("backend", cfg.backend)

    md = f"""# Alpaca Options Alpha Agent — Hackathon Write-Up

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
| IV-rank entry | Enter only when IV rank ≥ {cfg.iv_rank_min:.0f}. |
| Per-position risk | Max loss ≤ {cfg.max_portfolio_risk_pct:.0%} of equity ⇒ caps position size. |
| Capital ceiling | Net premium per position ≤ {cfg.max_single_position_pct:.0%} of equity. |
| Aggregate risk | Total defined risk across book ≤ {cfg.max_total_risk_pct:.0%} of equity. |
| Position count | At most {cfg.max_positions} concurrent positions; target {cfg.target_positions}. |
| Drawdown halt | New entries freeze if total drawdown ≥ {cfg.max_total_drawdown_pct:.0%}. |
| DTE window | Only {cfg.min_dte}–{cfg.max_dte} day expiries (income, not lottery). |

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
  {cfg.decision_interval_minutes} min) for the full 7-day window, fully
  unattended.

## 4. Performance (from latest run)

- Backend: **{backend}**
- Final equity: **${summary.get('final_equity', 100000):,.2f}**
- Total P&L: **${summary.get('total_pnl', 0):,.2f}** ({summary.get('return_pct', 0):.2f}%)
- Trades placed: **{summary.get('n_trades', 0)}**
- Max drawdown: **{summary.get('max_drawdown_pct', 0):.2f}%**

*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Paper-trading
results are hypothetical and not indicative of future performance.*
"""
    return md


def main() -> int:
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    run_json = ROOT / "runs" / "last_run.json"
    md = generate(run_json)
    (out_dir / "WRITEUP.md").write_text(md)
    (ROOT / "WRITEUP.md").write_text(md)
    print(f"Wrote {out_dir / 'WRITEUP.md'} ({len(md)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
