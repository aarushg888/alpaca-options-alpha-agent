"""Test the LiveMarket adapter against a stubbed CLI runner (no network).

The adapter builds the right CLI commands and maps real CLI output (chains +
snapshots) into the MarketView interface the strategy engine consumes. We test
it with a fake subprocess so it's deterministic and offline.
"""
import sys
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Config
from src.market.live_market import LiveMarket


def _fake_run(chain_json, snapshot_json):
    def runner(cmd):
        # 'option contracts' => chain, 'option snapshot' => snapshot
        if "contracts" in cmd:
            return {"option_contracts": chain_json}
        if "snapshot" in cmd:
            return snapshot_json
        return {}
    return runner


def _sample_chain():
    # One expiration, an ATM straddle for a ~100 underlying.
    return [
        {"symbol": "TST260918C00100000", "expiration_date": "2026-09-18",
         "type": "call", "strike_price": "100", "close_price": "3.50",
         "tradable": True, "status": "active"},
        {"symbol": "TST260918P00100000", "expiration_date": "2026-09-18",
         "type": "put", "strike_price": "100", "close_price": "2.50",
         "tradable": True, "status": "active"},
        {"symbol": "TST260918C00105000", "expiration_date": "2026-09-18",
         "type": "call", "strike_price": "105", "close_price": "1.20",
         "tradable": True, "status": "active"},
    ]


def _sample_snapshot():
    return {"snapshots": {}}


def test_live_market_recovers_underlying_via_parity():
    cfg = Config.from_env()
    # S ~ C - P + K = 3.5 - 2.5 + 100 = 101.0
    lm = LiveMarket(cfg, bin_path="alpaca")
    lm._runner = _fake_run(_sample_chain(), _sample_snapshot())
    spot = lm.get_underlying("TST")
    assert 100.0 < spot < 102.0


def test_live_market_iv_rank_tracks_window():
    cfg = Config.from_env()
    lm = LiveMarket(cfg, bin_path="alpaca")
    lm._runner = _fake_run(_sample_chain(), _sample_snapshot())
    # first call seeds history -> returns 50.0
    assert lm.iv_rank("TST") == 50.0


def test_live_market_price_option_uses_chain():
    cfg = Config.from_env()
    cfg.min_dte = 20
    cfg.max_dte = 40
    lm = LiveMarket(cfg, bin_path="alpaca")
    lm._runner = _fake_run(_sample_chain(), _sample_snapshot())
    # step to invalidate caches and re-fetch
    lm.step()
    px = lm.price_option("TST", "call", 100.0, dte_days=20, mid=True)
    assert px == 3.50  # from chain close_price


def test_live_market_chain_maps_to_contracts():
    cfg = Config.from_env()
    cfg.min_dte = 20
    cfg.max_dte = 40
    lm = LiveMarket(cfg, bin_path="alpaca")
    lm._runner = _fake_run(_sample_chain(), _sample_snapshot())
    lm.step()
    chain = lm.get_chain("TST")
    assert len(chain) == 3
    assert all(c.mid > 0 for c in chain)
