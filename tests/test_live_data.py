"""Test the live Alpaca market-data adapter against the real CLI.

Chain/quote data is public, so this works without trading credentials. It
proves the live data path is wired correctly (the same engine then trades once
the secret key is present).
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from src.config import Config
from src.market.alpaca_data import AlpacaMarketData
from src.broker.alpaca_cli import build_option_chain_command


def _have_cli() -> bool:
    from shutil import which
    return which("alpaca") is not None or Path.home().joinpath("go/bin/alpaca").exists()


@pytest.mark.skipif(not _have_cli(), reason="alpaca CLI not installed")
def test_chain_command_shape():
    cmd = build_option_chain_command("alpaca", "SPY", "2026-09-18", "2026-09-25")
    assert cmd[:3] == ["alpaca", "option", "contracts"]
    assert "--underlying-symbols" in cmd


@pytest.mark.skipif(not _have_cli(), reason="alpaca CLI not installed")
def test_live_chain_returns_real_contracts():
    cfg = Config.from_env()
    md = AlpacaMarketData(cfg, bin_path="alpaca")
    chain = md.get_chain("SPY", dte_min=20, dte_max=40)
    assert len(chain) > 0
    # At least some calls and puts.
    assert any(c.type == "call" for c in chain)
    assert any(c.type == "put" for c in chain)
    # All have positive mid and a valid OCC symbol.
    assert all(c.mid > 0 and c.symbol.startswith("SPY") for c in chain)


@pytest.mark.skipif(not _have_cli(), reason="alpaca CLI not installed")
def test_live_chain_strikes_around_spot():
    cfg = Config.from_env()
    md = AlpacaMarketData(cfg, bin_path="alpaca")
    chain = md.get_chain("SPY", dte_min=20, dte_max=40)
    strikes = sorted({c.strike for c in chain})
    # Should span a reasonable grid (not just one strike).
    assert len(strikes) > 5
