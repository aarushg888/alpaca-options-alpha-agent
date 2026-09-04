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
    # legs is valid JSON with ratio_qty as string
    idx = cmd.index("--legs") + 1
    legs = json.loads(cmd[idx])
    assert len(legs) == 4
    assert all("symbol" in l and "side" in l and l.get("ratio_qty") == "1" for l in legs)


def test_build_mleg_command_limit_when_price_set():
    o = _ic_order()
    o.limit_price = 2.84
    cmd = build_mleg_command("alpaca", o)
    assert "--type" in cmd and "limit" in cmd
    idx = cmd.index("--limit-price") + 1
    assert cmd[idx] == "2.84"


def test_build_mleg_command_market_when_no_price():
    cmd = build_mleg_command("alpaca", _ic_order())
    assert "--type" in cmd and "market" in cmd


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


def test_parse_occ_splits_symbol():
    from src.broker.alpaca_cli import parse_occ
    root, exp, cp, strike = parse_occ("SPY260930C00808000")
    assert root == "SPY" and exp == "2026-09-30" and cp == "C" and strike == 808.0
    root, exp, cp, strike = parse_occ("AMD261016P00420000")
    assert root == "AMD" and exp == "2026-10-16" and cp == "P" and strike == 420.0


def test_client_order_ids_unique_per_order():
    from src.broker.alpaca_cli import build_mleg_command
    a = build_mleg_command("alpaca", _ic_order())
    b = build_mleg_command("alpaca", _ic_order())
    assert a[a.index("--client-order-id") + 1] != b[b.index("--client-order-id") + 1]


def test_sync_positions_rebuilds_spreads_from_live_book():
    from src.config import Config
    from src.broker.alpaca_cli import AlpacaCliBroker

    cfg = Config.from_env()
    book = [
        {"symbol": "SPY260930C00808000", "qty": "-1", "avg_entry_price": "0.73",
         "unrealized_pl": "50"},
        {"symbol": "SPY260930C00839000", "qty": "1", "avg_entry_price": "0.09",
         "unrealized_pl": "-6"},
        {"symbol": "AMD261016P00440000", "qty": "-1", "avg_entry_price": "1.20",
         "unrealized_pl": "-65"},
        {"symbol": "AMD261016P00420000", "qty": "1", "avg_entry_price": "0.40",
         "unrealized_pl": "-20"},
    ]

    def runner(cmd):
        if "position" in cmd and "list" in cmd:
            return book
        return {}

    b = AlpacaCliBroker(cfg, bin_path="alpaca", runner=runner,
                        start_balance=100000.0)
    assert b.sync_positions() == 2
    poss = {p.root: p for p in b.get_positions()}
    assert set(poss) == {"SPY", "AMD"}
    assert abs(poss["SPY"].entry_credit - 0.64) < 1e-9
    assert abs(poss["SPY"].unrealized_pnl - 44.0) < 1e-9
    assert abs(poss["AMD"].entry_credit - 0.80) < 1e-9
