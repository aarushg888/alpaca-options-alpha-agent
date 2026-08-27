"""Black-Scholes option pricing with analytic Greeks.

This module is pure math (stdlib only) and is used by both the live and
simulated execution backends so pricing logic is identical in both paths.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class Greeks:
    delta: float
    gamma: float
    vega: float   # per 1.00 (100%) vol; multiply by 0.01 for per 1 vol point
    theta: float  # per year; divide by 365 for per day
    rho: float    # per 1.00 rate; multiply by 0.01 for per 1% rate


def price_and_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,  # "call" | "put"
    q: float = 0.0,    # continuous dividend yield
) -> tuple[float, Greeks]:
    """Return (price, Greeks) for a European option.

    T is time to expiry in years. sigma is annualized volatility (e.g. 0.20).
    Handles T<=0 and sigma<=0 gracefully (returns intrinsic / numeric limits).
    """
    if T <= 1e-9 or sigma <= 1e-9:
        # Intrinsic value when no time value.
        if option_type == "call":
            intrinsic = max(0.0, S - K)
            delta = 1.0 if S > K else 0.0
        else:
            intrinsic = max(0.0, K - S)
            delta = -1.0 if S < K else 0.0
        g = Greeks(delta=delta, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)
        return intrinsic, g

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT

    disc = math.exp(-r * T)
    div = math.exp(-q * T)

    if option_type == "call":
        price = S * div * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
        delta = div * _norm_cdf(d1)
        rho = K * T * disc * _norm_cdf(d2) / 100.0
    else:
        price = K * disc * _norm_cdf(-d2) - S * div * _norm_cdf(-d1)
        delta = -div * _norm_cdf(-d1)
        rho = -K * T * disc * _norm_cdf(-d2) / 100.0

    gamma = div * _norm_pdf(d1) / (S * sigma * sqrtT)
    vega = S * div * _norm_pdf(d1) * sqrtT / 100.0
    # Theta (per year)
    term = S * div * _norm_pdf(d1) * sigma / (2.0 * sqrtT)
    if option_type == "call":
        theta = -term - r * K * disc * _norm_cdf(d2)
    else:
        theta = -term + r * K * disc * _norm_cdf(-d2)
    theta = theta / 365.0  # per day

    g = Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)
    return price, g


def implied_vol(
    S: float,
    K: float,
    T: float,
    r: float,
    market_price: float,
    option_type: str,
    q: float = 0.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Bisection solver for implied volatility given a market price."""
    intrinsic = max(0.0, (S - K) if option_type == "call" else (K - S))
    if market_price <= intrinsic + 1e-9:
        return 1e-6  # essentially no vol

    low, high = 1e-4, 5.0
    for _ in range(max_iter):
        mid = 0.5 * (low + high)
        px, _ = price_and_greeks(S, K, T, r, mid, option_type, q)
        if px > market_price:
            high = mid
        else:
            low = mid
        if abs(high - low) < tol:
            break
    return 0.5 * (low + high)
