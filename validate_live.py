#!/usr/bin/env python3
"""ONE-SHOT live validation: place a single defined-risk paper option spread
through the Alpaca CLI to prove the execution path works end-to-end.

Safety: paper account only, single contract, defined risk. No looping.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config
from src.market.live_market import LiveMarket
from src.strategy.engine import generate_candidates, compute_signal
from src.broker.alpaca_cli import build_mleg_command

def main() -> int:
    cfg = Config.from_env()
    cfg.alpaca_api_key = "PKRRVMQZRU6YN7UYVEVHLDR2E3"
    cfg.alpaca_secret_key = "92teCZzzZJjp6w2yvvUPuCg68UnRYPDw2Epr1EJSp5Kj"
    lm = LiveMarket(cfg, bin_path="alpaca")
    for _ in range(3):
        lm.step()
    for u in cfg.universe:
        try: lm.iv_rank(u["symbol"])
        except Exception: pass

    sym = "SPY"
    sig = compute_signal(sym, lm, cfg)
    cands = generate_candidates(sym, lm, cfg, {sym: sig})
    ic = [c for c in cands if c.strategy == "iron_condor"]
    if not ic:
        print("No iron condor candidate for SPY now; trying any candidate.")
        c = cands[0] if cands else None
    else:
        c = ic[0]
    if not c:
        print("No candidate built."); return 2
    c.qty = 1
    c.limit_price = round(c.net_credit, 2)  # options require limit outside hours
    print(f"Strategy: {c.strategy} on {sym}  net credit ${c.net_credit:.2f}")
    cmd = build_mleg_command("alpaca", c)
    print("Submitting:", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    print("STDOUT:", res.stdout[:800])
    if res.stderr.strip():
        print("STDERR:", res.stderr[:300])
    return res.returncode

if __name__ == "__main__":
    raise SystemExit(main())
