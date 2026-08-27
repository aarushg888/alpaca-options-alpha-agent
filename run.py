#!/usr/bin/env python3
"""Backtest / simulated run of the Alpaca Options Alpha Agent.

This exercises the full pipeline (signals -> strategy -> risk gates -> executor
-> monitor) against the local market simulator with no external dependencies.
Usage:
    python run.py --steps 504 --seed 7
    python run.py --live        # uses Alpaca CLI if credentials are configured
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make `src` importable when run as a script.
ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.orchestrator import run, summarize  # noqa: E402
from src.market_sim import MarketSimulator  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Alpaca Options Alpha Agent — backtest/sim run")
    ap.add_argument("--steps", type=int, default=504)
    ap.add_argument("--seed", type=int, default=None, help="sim RNG seed")
    ap.add_argument("--live", action="store_true", help="use Alpaca CLI backend")
    ap.add_argument("--out", type=str, default="runs/last_run.json")
    ap.add_argument("--exec-log", type=str, default="runs/exec.log")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = Config.from_env()
    if args.live:
        # Force live: require credentials present.
        if cfg.backend != "alpaca":
            print("ERROR: --live requires ALPACA_API_KEY and ALPACA_SECRET_KEY.", file=sys.stderr)
            return 2
    else:
        cfg.alpaca_api_key = None
        cfg.alpaca_secret_key = None

    if args.seed is not None:
        cfg.sim_seed = args.seed

    sim = None
    if cfg.backend != "alpaca":
        sim = MarketSimulator(cfg, seed=cfg.sim_seed)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.exec_log).parent.mkdir(parents=True, exist_ok=True)

    result = run(cfg, steps=args.steps, sim=sim, exec_log=args.exec_log)
    summary = summarize(result)

    Path(args.out).write_text(json.dumps({
        "summary": summary, "trades": result.trades, "log": result.log,
    }, indent=2))

    print("=== RUN SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nTrades placed: {result.n_trades}")
    print(f"Full report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
