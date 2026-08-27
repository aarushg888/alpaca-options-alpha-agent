#!/usr/bin/env python3
"""Live competition runner for the Alpaca Options Alpha Agent.

Designed to run for the full hackathon window (Aug 28 – Sep 4) on a schedule
(e.g. cron every 15 min). Each tick:
  * reads the live Alpaca paper account via the Alpaca CLI,
  * proposes + risk-gates new defined-risk option spreads,
  * submits approved orders through the Alpaca CLI,
  * monitors/exit open positions.

Requires ALPACA_API_KEY + ALPACA_SECRET_KEY in the environment (or .env).
The agent will NOT trade real funds: Alpaca defaults to the paper environment.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.orchestrator import run, summarize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Alpaca Options Alpha Agent — live loop")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit")
    ap.add_argument("--interval", type=int, default=None,
                    help="minutes between ticks (default: config.decision_interval_minutes)")
    ap.add_argument("--steps", type=int, default=None, help="cap number of ticks")
    ap.add_argument("--out", type=str, default="runs/live_run.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if cfg.backend != "alpaca":
        print("ERROR: live mode requires ALPACA_API_KEY and ALPACA_SECRET_KEY "
              "in the environment / .env.", file=sys.stderr)
        return 2
    if not cfg.alpaca_paper:
        print("SAFETY: refusing to run against a LIVE (non-paper) account.",
              file=sys.stderr)
        return 3

    interval = args.interval or cfg.decision_interval_minutes
    steps = args.steps or 1
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    logging.info("Starting live paper agent (backend=alpaca, paper=%s)", cfg.alpaca_paper)
    result = run(cfg, steps=steps)
    summary = summarize(result)
    Path(args.out).write_text(__import__("json").dumps({
        "summary": summary, "trades": result.trades, "log": result.log
    }, indent=2))
    logging.info("Tick complete: %s", summary)

    if not args.once:
        logging.info("Sleeping %s min until next tick...", interval)
        time.sleep(interval * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
