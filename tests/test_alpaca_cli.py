"""Tests for the Alpaca CLI command construction (pure, no credentials)."""
import sys
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.broker.alpaca_cli import (
    build_mleg_command, build_close_command, build_account_command,
    build_cancel_all_command,
)
from src.broker.base import Leg, Order


def _ic_order() -> Order:
    return Order(
        order_class="mleg", strategy="iron_condor", root="SPY", qty=1,
        legs=[
            Leg(symbol="SPY250919P00580000", side="sell", root="SPY",
                asset_class="option", type="put", strike=580, expiry_day=30),
            Leg(symbol="SPY250919P00575000", side="buy", root="SPY",
                asset_class="option", type="put", strike=575, expiry_day=30),
            Leg(symbol="SPY250919C00600000", side="sell", root="SPY",
                asset_class="option", type="call", strike=600, expiry_day=30),
            Leg(symbol="SPY250919C00605000", side="buy", root="SPY",
                asset_class="option", type="call", strike=605, expiry_day=30),
        ],
    )


def test_build_mleg_command_has_required_flags():
    cmd = build_mleg_command("alpaca", _ic_order())
    assert cmd[0] == "alpaca"
    assert "order" in cmd and "submit" in cmd
    assert "--order-class" in cmd and "mleg" in cmd
    # legs is valid JSON
    idx = cmd.index("--legs") + 1
    legs = json.loads(cmd[idx])
    assert len(legs) == 4
    assert all("symbol" in l and "side" in l for l in legs)


def test_build_close_command_targets_root():
    from src.broker.base import Position
    pos = Position(id="p1", strategy="iron_condor", root="SPY",
                   legs=_ic_order().legs, qty=1, opened_day=0, expiry_day=30,
                   entry_credit=1.0)
    cmd = build_close_command("alpaca", pos)
    assert "--symbol-or-asset-id" in cmd
    idx = cmd.index("--symbol-or-asset-id") + 1
    assert cmd[idx] == "SPY"


def test_build_account_command():
    cmd = build_account_command("alpaca")
    assert "account" in cmd and "get" in cmd
    assert "--jq" in cmd


def test_build_cancel_all_command():
    cmd = build_cancel_all_command("alpaca")
    assert "cancel-all" in cmd


def test_alpaca_backend_with_stub_runner():
    """Exercise AlpacaCliBroker using a stub runner (no network/credentials)."""
    from src.config import Config
    from src.broker.alpaca_cli import AlpacaCliBroker
    from src.broker.base import Position

    cfg = Config.from_env()
    calls = []

    def runner(cmd):
        calls.append(cmd)
        if "account" in cmd:
            return {"equity": "100000.0", "cash": "100000.0",
                    "buying_power": "100000.0", "day_trade_count": 0,
                    "status": "ACTIVE", "long_market_value": "0.0"}
        if "order" in cmd and "submit" in cmd:
            return {"id": "alp_1", "client_order_id": "iron_condor-SPY",
                    "status": "accepted"}
        if "position" in cmd and "close" in cmd:
            return {"status": "closed"}
        return {}

    b = AlpacaCliBroker(cfg, bin_path="alpaca", runner=runner,
                        start_balance=100000.0)
    acct = b.get_account()
    assert acct.equity == 100000.0
    res = b.submit_order(_ic_order())
    assert res.ok
    pos = Position(id="alp_1", strategy="iron_condor", root="SPY",
                   legs=_ic_order().legs, qty=1, opened_day=0, expiry_day=30,
                   entry_credit=1.0)
    b._positions["alp_1"] = pos
    res2 = b.close_position("alp_1", reason="test")
    assert res2.ok
    assert len(calls) >= 3
