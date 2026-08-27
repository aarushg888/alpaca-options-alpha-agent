"""Local market simulator for the options alpha agent.

Provides a deterministic, pandas-free simulation of:
  - Underlying prices evolving via geometric Brownian motion (GBM)
  - A per-symbol implied-volatility surface that mean-reverts through
    "regimes" (low / normal / high IV) so the agent sees varying IV rank
  - On-demand option chains priced with Black-Scholes (see finance/)

This lets the full agent — strategy, risk, execution, monitor — run and be
tested end-to-end with zero external dependencies and no Alpaca credentials.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from src.finance.black_scholes import price_and_greeks

TRADING_DAYS = 252


def make_occ_symbol(root: str, expiry_yyyymmdd: str, otype: str, strike: float) -> str:
    """OCC-style option symbol, e.g. SPY250919P00580000."""
    cp = "C" if otype == "call" else "P"
    strike6 = f"{int(round(strike * 1000)):06d}"
    return f"{root}{expiry_yyyymmdd}{cp}{strike6}"


def yyyymmdd_from_day(day: int) -> str:
    # Day 0 maps to an arbitrary near-future base date for realistic symbols.
    base = 250919  # YYMMDD (2025-09-19)
    # Simple additive date that keeps month/day formatting good enough for sim.
    return str(base + day * 100).zfill(7)[-7:] if False else _fmt_day(day)


def _fmt_day(day: int) -> str:
    year = 25 + (day // 365)
    rem = day % 365
    month = 1 + (rem // 31) % 12
    d = 1 + (rem % 28)
    return f"{year:02d}{month:02d}{d:02d}"


@dataclass
class Contract:
    symbol: str          # OCC symbol
    root: str
    type: str            # "call" | "put"
    strike: float
    expiry_day: int      # absolute sim day of expiry
    expiry_yyyymmdd: str
    iv: float
    bid: float
    ask: float
    mid: float
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0

    @property
    def dte(self) -> int:
        return self.expiry_day


@dataclass
class UnderlyingState:
    symbol: str
    price: float
    base_iv: float
    mu: float
    sigma: float
    iv_state: float = 0.5  # [0,1] mean-reverting IV regime driver


class MarketSimulator:
    def __init__(self, config, day: int = 0, seed: Optional[int] = None):
        self.config = config
        self.day = day
        self.rng = random.Random(seed if seed is not None else config.sim_seed)
        self.underlyings: dict[str, UnderlyingState] = {}
        for u in config.universe:
            self.underlyings[u["symbol"]] = UnderlyingState(
                symbol=u["symbol"],
                price=float(u["lvl"]),
                base_iv=config.sim_iv_base,
                mu=config.sim_mu,
                sigma=config.sim_sigma,
                iv_state=self.rng.uniform(0.35, 0.65),
            )

    # ---- time / underlying dynamics -------------------------------------
    def step(self) -> None:
        """Advance one step (default = one trading day)."""
        self.day += self.config.sim_days_per_step
        dt = self.config.sim_days_per_step / TRADING_DAYS
        for st in self.underlyings.values():
            z = self.rng.gauss(0.0, 1.0)
            st.price *= math.exp(
                (st.mu - 0.5 * st.sigma**2) * dt + st.sigma * math.sqrt(dt) * z
            )
            st.price = max(0.5, st.price)
            # Mean-reverting IV regime (OU-ish) via AR(1) toward 0.5
            kappa = 0.05
            st.iv_state += kappa * (0.5 - st.iv_state) + 0.06 * self.rng.gauss(0, 1)
            st.iv_state = min(0.98, max(0.02, st.iv_state))

    def get_underlying(self, symbol: str) -> float:
        return self.underlyings[symbol].price

    def iv_rank(self, symbol: str) -> float:
        """Map IV regime state -> a 0-100 'IV rank' style number."""
        return round(self.underlyings[symbol].iv_state * 100.0, 1)

    def _contract_iv(self, st: UnderlyingState, strike: float, T: float) -> float:
        # Term structure: longer expiry => richer IV; skew: OTM puts richer.
        term = 1.0 + self.config.sim_iv_term * (T * TRADING_DAYS) / 60.0
        money = strike / st.price
        skew = 1.0 + 0.12 * max(0.0, (1.0 - money))  # puts slightly richer
        skew = skew * (1.0 + 0.06 * max(0.0, (money - 1.0)))  # calls mild
        iv = st.base_iv * term * skew * (0.7 + 0.6 * st.iv_state)
        return max(0.03, iv)

    # ---- option chain --------------------------------------------------
    def get_chain(self, symbol: str, dte_min: int | None = None,
                  dte_max: int | None = None) -> list[Contract]:
        st = self.underlyings[symbol]
        dte_min = dte_min if dte_min is not None else self.config.min_dte
        dte_max = dte_max if dte_max is not None else self.config.max_dte
        contracts: list[Contract] = []
        # Candidate expiries at weekly spacing within DTE window.
        exp_days = range(
            self.day + dte_min, self.day + dte_max + 1, 7
        )
        # Strike grid around spot.
        spot = st.price
        strikes = [round(spot * (1 + 0.02 * k), 2) for k in range(-6, 7)]
        for eday in exp_days:
            expiry_y = _fmt_day(eday)
            T = (eday - self.day) / TRADING_DAYS
            for K in strikes:
                for otype in ("call", "put"):
                    iv = self._contract_iv(st, K, T)
                    px, g = price_and_greeks(
                        S=spot, K=K, T=T, r=self.config.sim_risk_free,
                        sigma=iv, option_type=otype,
                    )
                    px = max(0.01, px)
                    half_spread = max(0.03, px * 0.04)
                    contracts.append(Contract(
                        symbol=make_occ_symbol(symbol, expiry_y, otype, K),
                        root=symbol, type=otype, strike=K, expiry_day=eday,
                        expiry_yyyymmdd=expiry_y, iv=iv,
                        bid=max(0.01, px - half_spread),
                        ask=px + half_spread, mid=px,
                        delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega,
                    ))
        return contracts

    def price_option(self, symbol: str, otype: str, strike: float,
                     dte_days: int, mid: bool = True) -> float:
        """Price an arbitrary (symbol, type, strike, dte) using current state."""
        st = self.underlyings[symbol]
        T = max(1e-6, dte_days / TRADING_DAYS)
        iv = self._contract_iv(st, strike, T)
        px, _ = price_and_greeks(
            S=st.price, K=strike, T=T, r=self.config.sim_risk_free,
            sigma=iv, option_type=otype,
        )
        return max(0.01, px) if mid else max(0.01, px)
