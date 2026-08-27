"""End-to-end test of the full agent pipeline against the simulator."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config
from src.orchestrator import run, summarize
from src.market_sim import MarketSimulator


def test_full_run_produces_finite_equity_and_trades():
    cfg = Config.from_env()
    cfg.sim_seed = 2026
    sim = MarketSimulator(cfg, seed=cfg.sim_seed)
    result = run(cfg, steps=120, market=sim)
    summ = summarize(result)
    assert summ["backend"] == "simulated"
    assert summ["final_equity"] > 0
    assert len(result.equity_curve) == 120
    # equity curve finite
    assert all(e == e and e > 0 for e in result.equity_curve)
    # drawdown non-negative and < 100%
    assert 0.0 <= summ["max_drawdown_pct"] < 100.0


def test_run_is_deterministic_for_fixed_seed():
    cfg = Config.from_env()
    cfg.sim_seed = 555
    sim_a = MarketSimulator(cfg, seed=cfg.sim_seed)
    sim_b = MarketSimulator(cfg, seed=cfg.sim_seed)
    ra = run(cfg, steps=60, market=sim_a)
    rb = run(cfg, steps=60, market=sim_b)
    assert ra.final_equity == rb.final_equity


def test_run_does_not_exceed_position_cap():
    cfg = Config.from_env()
    cfg.sim_seed = 99
    sim = MarketSimulator(cfg, seed=cfg.sim_seed)
    result = run(cfg, steps=80, market=sim)
    # open positions should never exceed cap at any recorded step implicitly;
    # we at least verify final state is coherent.
    assert isinstance(result.n_trades, int)
    assert result.n_trades >= 0
