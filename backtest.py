#!/usr/bin/env python3
"""Run N backtests across seeds and print a summary table + aggregate stats.
Used to understand strategy variance before committing tuning changes."""
from __future__ import annotations

import json
import sys
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.orchestrator import run, summarize  # noqa: E402
from src.market_sim import MarketSimulator  # noqa: E402


def main() -> int:
    seeds = [int(s) for s in sys.argv[1:]] or [1, 7, 42, 1234, 2026, 555, 99, 313, 867, 4242]
    cfg = Config.from_env()
    pnls, mdd, trades = [], [], []
    print(f"{'seed':>6} | {'P&L':>11} | {'ret%':>7} | {'mdd%':>6} | {'trades':>6}")
    print("-" * 48)
    for s in seeds:
        cfg.sim_seed = s
        sim = MarketSimulator(cfg, seed=s)
        res = run(cfg, steps=504, sim=sim)
        sm = summarize(res)
        pnls.append(sm["total_pnl"])
        mdd.append(sm["max_drawdown_pct"])
        trades.append(sm["n_trades"])
        print(f"{s:>6} | ${sm['total_pnl']:>9,.0f} | {sm['return_pct']:>6.2f} | "
              f"{sm['max_drawdown_pct']:>5.2f} | {sm['n_trades']:>6}")
    print("-" * 48)
    print(f"{'AVG':>6} | ${statistics.mean(pnls):>9,.0f} | "
          f"{100*statistics.mean(pnls)/100000:>6.2f} | {statistics.mean(mdd):>5.2f} | "
          f"{statistics.mean(trades):>6.0f}")
    print(f"{'MIN':>6} | ${min(pnls):>9,.0f}")
    print(f"{'MAX':>6} | ${max(pnls):>9,.0f}")
    print(f"win rate: {sum(1 for p in pnls if p > 0)/len(pnls):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
