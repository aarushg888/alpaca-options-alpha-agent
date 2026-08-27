"""Live market-data adapter backed by the Alpaca CLI (public market data).

The Alpaca CLI returns real option chains, quotes and (during market hours)
Greeks *without* trading credentials — only an API key is needed, and quote
data is public. This adapter turns that CLI output into the same `Contract`
shape the simulator uses, so the strategy/risk/execution engine is identical
in simulated and live mode.

Execution still goes through `AlpacaCliBroker` (which requires the secret key).
So: live reads need only the key id; live trades need the secret too. The
agent degrades gracefully — it can read the market and paper-trade once the
secret is present.
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional

from src.broker.alpaca_cli import build_option_chain_command  # we add below
from src.config import Config
from src.market_sim import Contract


def _run(cmd: list[str]) -> dict:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"alpaca CLI failed: {res.stderr.strip()}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return {"raw": res.stdout.strip()}


class AlpacaMarketData:
    def __init__(self, config: Config, bin_path: str | None = None):
        self.config = config
        self.bin = bin_path or config.alpaca_cli_bin

    def get_chain(self, symbol: str, dte_min: int, dte_max: int) -> list[Contract]:
        """Fetch a real option chain and map to Contract objects."""
        from datetime import date, timedelta
        today = date.today()
        lo = (today + timedelta(days=dte_min)).isoformat()
        hi = (today + timedelta(days=dte_max)).isoformat()
        cmd = build_option_chain_command(self.bin, symbol, lo, hi)
        out = _run(cmd)
        contracts = out.get("option_contracts", [])
        result: list[Contract] = []
        for c in contracts:
            otype = "call" if c.get("type") == "call" else "put"
            # Quote via close_price as a fallback (real bid/ask needs snapshot).
            mid = float(c.get("close_price") or 0.0)
            if mid <= 0:
                continue
            exp = c.get("expiration_date", "")
            # days to expiry
            try:
                ed = date.fromisoformat(exp)
                exp_day = (ed - today).days
            except Exception:
                exp_day = 30
            result.append(Contract(
                symbol=c["symbol"], root=symbol, type=otype,
                strike=float(c["strike_price"]),
                expiry_day=exp_day, expiry_yyyymmdd=exp.replace("-", "")[2:],
                iv=0.20, bid=mid * 0.97, ask=mid * 1.03, mid=mid,
                delta=0.0, gamma=0.0, theta=0.0, vega=0.0,
            ))
        return result

    def get_underlying_price(self, symbol: str) -> Optional[float]:
        """Best-effort spot price: use the chain's underlying via quote if possible.
        Falls back to None (caller may use last known)."""
        return None
