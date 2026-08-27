"""Tests for the deterministic risk gates (the safety core)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.broker.base import Leg, Order, Position, Account
from src.config import Config
from src.risk.gates import RiskGates, defined_risk, spread_width
from src.market_sim import MarketSimulator


def _credit_spread(credit: float, width: float) -> Order:
    # put credit spread: sell 100-strike, buy (100-width)-strike
    return Order(
        order_class="mleg", strategy="put_credit_spread", root="TEST",
        qty=1,
        legs=[
            Leg(symbol="X", side="sell", root="TEST", asset_class="option",
                type="put", strike=100.0, expiry_day=30, entry_price=2.0),
            Leg(symbol="Y", side="buy", root="TEST", asset_class="option",
                type="put", strike=100.0 - width, expiry_day=30,
                entry_price=2.0 - credit),
        ],
    )


def test_net_credit_positive():
    o = _credit_spread(credit=1.0, width=5.0)
    assert o.net_credit == 1.0


def test_defined_risk_width_minus_credit():
    o = _credit_spread(credit=1.0, width=5.0)
    assert defined_risk(o) == 4.0
    assert spread_width(o) == 5.0


def test_rejects_non_credit_order():
    cfg = Config.from_env()
    g = RiskGates(cfg)
    bad = _credit_spread(credit=-0.5, width=5.0)  # debit => reject
    acct = Account(100000, 100000, 100000, 0, 0, 0)
    v = g.evaluate(bad, acct, [])
    assert not v.allow


def test_rejects_when_iv_rank_too_low():
    cfg = Config.from_env()
    g = RiskGates(cfg)
    o = _credit_spread(credit=1.0, width=5.0)
    acct = Account(100000, 100000, 100000, 0, 0, 0)
    v = g.evaluate(o, acct, [], symbol_iv_rank=10.0)  # below min
    assert not v.allow


def test_allows_good_order_with_quantity_cap():
    cfg = Config.from_env()
    g = RiskGates(cfg)
    o = _credit_spread(credit=1.0, width=5.0)  # risk $4/contract
    acct = Account(100000, 100000, 100000, 0, 0, 0)
    # per-position risk cap = 0.04*100000 = 4000 => 1000 contracts possible
    # aggregate/position count gating still applies.
    v = g.evaluate(o, acct, [], symbol_iv_rank=50.0)
    assert v.allow
    assert v.max_qty >= 1


def test_aggregate_risk_caps_new_entries():
    cfg = Config.from_env()
    g = RiskGates(cfg)
    o = _credit_spread(credit=1.0, width=5.0)  # $4 risk/contract
    acct = Account(100000, 100000, 100000, 0, 0, 0)
    # Pre-existing positions using most of aggregate budget.
    existing = [
        Position(id="p1", strategy="put_credit_spread", root="A",
                 legs=o.legs, qty=900, opened_day=0, expiry_day=30,
                 entry_credit=1.0),
    ]
    v = g.evaluate(o, acct, existing, symbol_iv_rank=50.0)
    # aggregate cap 0.20*100000=20000 ; existing uses 900*4=3600, room=16400
    # => max_qty ~ 4100 but position-count ceiling (8-1=7) wins here.
    assert v.max_qty <= cfg.max_positions


def test_drawdown_halt():
    cfg = Config.from_env()
    g = RiskGates(cfg)
    acct = Account(85000, 85000, 85000, 0, -15000, 0)  # 15% loss
    halt, reason = g.drawdown_halt(acct, peak_equity=100000.0)
    assert halt
