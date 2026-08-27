"""Tests for the market simulator and strategy engine."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config
from src.market_sim import MarketSimulator, make_occ_symbol
from src.strategy.engine import (
    compute_signal, build_iron_condor, build_put_credit_spread, classify_iv_rank,
)
from src.broker.simulated_broker import SimulatedBroker


def test_simulator_steps_and_prices_stay_positive():
    cfg = Config.from_env()
    sim = MarketSimulator(cfg, seed=42)
    p0 = sim.get_underlying("SPY")
    for _ in range(20):
        sim.step()
    p1 = sim.get_underlying("SPY")
    assert p0 > 0 and p1 > 0


def test_chain_has_calls_and_puts():
    cfg = Config.from_env()
    sim = MarketSimulator(cfg, seed=1)
    chain = sim.get_chain("SPY")
    assert any(c.type == "call" for c in chain)
    assert any(c.type == "put" for c in chain)
    assert all(c.bid <= c.ask for c in chain)


def test_iv_rank_in_range():
    cfg = Config.from_env()
    sim = MarketSimulator(cfg, seed=7)
    for _ in range(10):
        sim.step()
    ivr = sim.iv_rank("SPY")
    assert 0 <= ivr <= 100


def test_classify_iv_rank():
    assert classify_iv_rank(20) == "low_iv"
    assert classify_iv_rank(50) == "normal_iv"
    assert classify_iv_rank(85) == "high_iv"


def test_signal_uses_regime():
    cfg = Config.from_env()
    sim = MarketSimulator(cfg, seed=3)
    sig = compute_signal("SPY", sim, cfg)
    assert sig.regime in ("low_iv", "normal_iv", "high_iv")
    assert 0 <= sig.score <= 1


def test_build_iron_condor_returns_net_credit_order():
    cfg = Config.from_env()
    sim = MarketSimulator(cfg, seed=9)
    sim.step()
    ic = build_iron_condor("SPY", sim, cfg)
    assert ic is not None
    assert ic.order_class == "mleg"
    assert len(ic.legs) == 4
    assert ic.net_credit > 0


def test_simulation_equity_stays_finite_and_documented():
    cfg = Config.from_env()
    sim = MarketSimulator(cfg, seed=11)
    broker = SimulatedBroker(cfg, sim)
    for _ in range(30):
        sim.step()
        broker.step_market()
    acct = broker.get_account()
    assert acct.equity > 0
    assert acct.equity == acct.cash + acct.total_pnl
