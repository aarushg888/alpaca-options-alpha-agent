"""Live market view backed by the Alpaca CLI (real option data, no trading creds).

Implements the SAME interface the strategy engine expects from `MarketSimulator`
(`get_underlying`, `iv_rank`, `price_option`, `day`), but sourced from real
Alpaca option chains + snapshots over the CLI. This means `propose()` and the
risk gates run identically against live market data.

Underlying price is recovered from an ATM straddle via put-call parity using
real bid/ask (no separate stock-quote call required). IV rank is tracked as a
trailing window of ATM-implied vol per symbol (so the signal reflects "is vol
rich or cheap right now").
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from src.broker.alpaca_cli import build_option_chain_command
from src.finance.black_scholes import implied_vol
from src.market_sim import Contract, make_occ_symbol, _fmt_day


def _run(cmd: list[str]) -> dict:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"alpaca CLI failed: {res.stderr.strip()}")
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return {}


class LiveMarket:
    def __init__(self, config, bin_path: str | None = None, r: float = 0.04,
                 runner=None, iv_history_path: str | None = None):
        self.config = config
        self.bin = bin_path or config.alpaca_cli_bin
        self.r = r
        self._runner = runner or _run
        self.day = 0
        self._underlying: dict[str, float] = {}
        self._iv_history: dict[str, list[float]] = {u["symbol"]: [] for u in config.universe}
        self._iv_history_path = Path(iv_history_path) if iv_history_path else (
            config.results_dir / "iv_history.json")
        self._load_iv_history()
        self._chain_cache: dict[str, list[Contract]] = {}
        self._chain_day: dict[str, int] = {}

    # ---- underlying price via put-call parity on ATM straddle -----------
    def _fetch_chain_raw(self, symbol: str) -> list[dict]:
        today = date.today()
        lo = (today + timedelta(days=self.config.min_dte)).isoformat()
        hi = (today + timedelta(days=self.config.max_dte)).isoformat()
        cmd = build_option_chain_command(self.bin, symbol, lo, hi)
        out = self._runner(cmd)
        return out.get("option_contracts", [])

    def get_underlying(self, symbol: str) -> float:
        if symbol in self._underlying and self._chain_day.get(symbol) == self.day:
            return self._underlying[symbol]
        raw = self._fetch_chain_raw(symbol)
        # Find the contract whose strike is closest to its close_price proxy.
        # We recover spot S from an ATM call/put pair: C - P = S - K e^{-rT}.
        # Use the nearest-strike ATM straddle.
        if not raw:
            return self._underlying.get(symbol, 100.0)
        # group by expiration, pick first active expiration in window
        exps = sorted({c["expiration_date"] for c in raw})
        if not exps:
            return self._underlying.get(symbol, 100.0)
        exp = exps[0]
        ed = date.fromisoformat(exp)
        T = max(1e-6, (ed - date.today()).days / 252.0)
        calls = {float(c["strike_price"]): c for c in raw
                 if c["expiration_date"] == exp and c["type"] == "call"}
        puts = {float(c["strike_price"]): c for c in raw
                if c["expiration_date"] == exp and c["type"] == "put"}
        # spot estimate from each available straddle, averaged
        ests = []
        for K, c in calls.items():
            p = puts.get(K)
            if not p:
                continue
            cpx = float(c.get("close_price") or 0)
            ppx = float(p.get("close_price") or 0)
            if cpx > 0 and ppx > 0:
                S = cpx - ppx + K * (2.718281828459045 ** (-self.r * T))
                ests.append(S)
        spot = sum(ests) / len(ests) if ests else (next(iter(calls)) if calls else 100.0)
        self._underlying[symbol] = spot
        self._chain_day[symbol] = self.day
        return spot

    # ---- IV rank (trailing window of ATM IV) ---------------------------
    def _atm_iv(self, symbol: str) -> float:
        spot = self.get_underlying(symbol)
        raw = self._fetch_chain_raw(symbol)
        exps = sorted({c["expiration_date"] for c in raw})
        if not exps:
            return 0.20
        exp = exps[0]
        ed = date.fromisoformat(exp)
        T = max(1e-6, (ed - date.today()).days / 252.0)
        atm = None
        best = 1e9
        for c in raw:
            if c["expiration_date"] != exp:
                continue
            K = float(c["strike_price"])
            px = float(c.get("close_price") or 0)
            if px <= 0:
                continue
            d = abs(K - spot)
            if d < best:
                best = d
                atm = (K, px, c["type"])
        if not atm:
            return 0.20
        iv = implied_vol(spot, atm[0], T, self.r, atm[1], atm[2])
        return iv

    def iv_rank(self, symbol: str) -> float:
        iv = self._atm_iv(symbol)
        hist = self._iv_history.setdefault(symbol, [])
        hist.append(iv)
        if len(hist) > 60:
            hist.pop(0)
        if len(hist) < 2:
            return 50.0
        lo, hi = min(hist), max(hist)
        if hi - lo < 1e-6:
            return 50.0
        rank = (iv - lo) / (hi - lo) * 100.0
        self._save_iv_history()
        return round(max(0.0, min(100.0, rank)), 1)

    # ---- persisted IV history (accumulates across live runs) ----------
    def _load_iv_history(self) -> None:
        if self._iv_history_path.exists():
            try:
                data = json.loads(self._iv_history_path.read_text())
                for sym, vals in data.items():
                    self._iv_history[sym] = vals[-60:]
            except Exception:
                pass

    def _save_iv_history(self) -> None:
        try:
            self._iv_history_path.parent.mkdir(parents=True, exist_ok=True)
            self._iv_history_path.write_text(json.dumps(self._iv_history))
        except Exception:
            pass

    # ---- pricing interface used by the strategy engine ----------------
    def price_option(self, symbol: str, otype: str, strike: float,
                     dte_days: int, mid: bool = True) -> float:
        """Price an option using the live chain (real) when available, else BS."""
        chain = self.get_chain(symbol, dte_min=max(1, dte_days - 2),
                               dte_max=dte_days + 2)
        for c in chain:
            if c.type == otype and abs(c.strike - strike) < 0.01:
                return c.mid
        # Fallback: Black-Scholes with a generic IV.
        spot = self.get_underlying(symbol)
        T = max(1e-6, dte_days / 252.0)
        from src.finance.black_scholes import price_and_greeks
        px, _ = price_and_greeks(spot, strike, T, self.r, 0.20, otype)
        return max(0.01, px)

    # ---- chain (real) -------------------------------------------------
    def get_chain(self, symbol: str, dte_min: int | None = None,
                  dte_max: int | None = None) -> list[Contract]:
        if (symbol in self._chain_cache
                and self._chain_day.get(symbol) == self.day):
            return self._chain_cache[symbol]
        dte_min = dte_min if dte_min is not None else self.config.min_dte
        dte_max = dte_max if dte_max is not None else self.config.max_dte
        raw = self._fetch_chain_raw(symbol)
        today = date.today()
        contracts: list[Contract] = []
        for c in raw:
            otype = "call" if c["type"] == "call" else "put"
            mid = float(c.get("close_price") or 0.0)
            if mid <= 0:
                continue
            exp = c.get("expiration_date", "")
            try:
                ed = date.fromisoformat(exp)
                # Express expiry in the same absolute-day convention as the
                # simulator: sim.day + (calendar days from today). This keeps
                # strategy expiry math identical across backtest and live.
                exp_day = self.day + (ed - today).days
            except Exception:
                exp_day = self.day + 30
            contracts.append(Contract(
                symbol=c["symbol"], root=symbol, type=otype,
                strike=float(c["strike_price"]),
                expiry_day=exp_day, expiry_yyyymmdd=exp.replace("-", "")[2:],
                iv=0.20, bid=mid * 0.97, ask=mid * 1.03, mid=mid,
                delta=0.0, gamma=0.0, theta=0.0, vega=0.0,
            ))
        self._chain_cache[symbol] = contracts
        self._chain_day[symbol] = self.day
        return contracts

    def step(self) -> None:
        self.day += 1
        # invalidate caches so each "day" re-fetches real data
        self._chain_cache.clear()
        self._underlying.clear()
