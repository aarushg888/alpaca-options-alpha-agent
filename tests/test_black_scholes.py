"""Tests for Black-Scholes pricing and Greeks."""
from src.finance.black_scholes import price_and_greeks, implied_vol


def test_call_put_parity():
    S, K, T, r, sig = 100.0, 100.0, 1.0, 0.05, 0.20
    c, _ = price_and_greeks(S, K, T, r, sig, "call")
    p, _ = price_and_greeks(S, K, T, r, sig, "put")
    # C - P = S - K e^{-rT}
    parity = c - p
    expected = S - K * (1 - r * T)  # approx for small rT without dividend
    # use exact discount:
    expected = S - K * (2.718281828459045 ** (-r * T))
    assert abs(parity - expected) < 0.05


def test_call_delta_positive_in_bounds():
    c, g = price_and_greeks(100.0, 100.0, 1.0, 0.05, 0.20, "call")
    assert 0.0 < g.delta < 1.0
    assert g.gamma > 0
    assert g.vega > 0


def test_put_delta_negative_in_bounds():
    p, g = price_and_greeks(100.0, 100.0, 1.0, 0.05, 0.20, "put")
    assert -1.0 < g.delta < 0.0


def test_deep_itm_call_approx_intrinsic():
    c, _ = price_and_greeks(200.0, 100.0, 0.5, 0.05, 0.20, "call")
    assert c > 95.0  # ~ S - K discounted


def test_implied_vol_recovers_input():
    S, K, T, r, sig = 150.0, 155.0, 0.5, 0.04, 0.35
    px, _ = price_and_greeks(S, K, T, r, sig, "put")
    iv = implied_vol(S, K, T, r, px, "put")
    assert abs(iv - sig) < 1e-3
