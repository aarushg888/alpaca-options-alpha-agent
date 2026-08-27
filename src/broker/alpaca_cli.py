"""Live Alpaca broker backend that drives the official Alpaca CLI.

This is the production execution path for the hackathon. It shells out to the
`alpaca` CLI (github.com/alpacahq/cli) — satisfying the competition requirement
that projects "utilize either Alpaca's MCP server or its CLI tools."

Key design:
  * Command construction is pure (build_* functions) so it is unit-tested
    without credentials or network.
  * Execution goes through a thin `_run()` wrapper so tests can stub it.
  * Multi-leg option spreads use `--order-class mleg --legs '<json>'`.

Alpaca API credentials are read from the environment (ALPACA_API_KEY /
ALPACA_SECRET_KEY). The CLI's own profile/config sourcing handles auth; this
module only emits commands and parses JSON output.
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional

from src.broker.base import (
    Account, BrokerBackend, Leg, Order, Position, SubmitResult,
)


def _leg_to_cli(leg: Leg) -> dict:
    return {"symbol": leg.symbol, "side": leg.side, "qty": leg.qty}


def build_option_chain_command(bin_path: str, underlying: str,
                                exp_gte: str, exp_lte: str) -> list[str]:
    """Fetch the real option chain via the Alpaca CLI (public market data)."""
    return [bin_path, "option", "contracts",
            "--underlying-symbols", underlying,
            "--expiration-date-gte", exp_gte,
            "--expiration-date-lte", exp_lte,
            "--limit", "10000"]


def build_mleg_command(bin_path: str, order: Order) -> list[str]:
    """Construct the `alpaca order submit` CLI args for a multi-leg spread."""
    legs = [_leg_to_cli(lg) for lg in order.legs]
    cmd = [
        bin_path, "order", "submit",
        "--order-class", "mleg",
        "--type", "market",
        "--time-in-force", order.time_in_force,
        "--legs", json.dumps(legs),
        "--client-order-id", f"{order.strategy}-{order.root}",
    ]
    return cmd


def build_close_command(bin_path: str, position: Position) -> list[str]:
    """Close every option leg of a multi-leg position by symbol.

    Alpaca's `position close` works per underlying symbol; for multi-leg
    option positions we close the whole position via the underlying root.
    """
    return [bin_path, "position", "close",
            "--symbol-or-asset-id", position.root, "--qty", str(100 * position.qty)]


def build_cancel_all_command(bin_path: str) -> list[str]:
    return [bin_path, "order", "cancel-all"]


def build_account_command(bin_path: str) -> list[str]:
    return [bin_path, "account", "get", "--jq",
            "{equity: .equity, cash: .cash, buying_power: .buying_power, "
            "day_trade_count: .day_trade_count, status: .status, "
            "long_market_value: .long_market_value}"]


class AlpacaCliBroker(BrokerBackend):
    def __init__(self, config, bin_path: str | None = None, runner=None,
                 start_balance: float | None = None):
        self.config = config
        self.bin = bin_path or config.alpaca_cli_bin
        self._run = runner or self._subprocess_run
        self.starting = start_balance if start_balance is not None else config.starting_balance
        self._positions: dict[str, Position] = {}
        self._day = 0

    # ---- execution helpers --------------------------------------------
    def _subprocess_run(self, cmd: list[str]) -> dict:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"alpaca CLI failed: {res.stderr.strip()}")
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            return {"raw": res.stdout.strip()}

    # ---- BrokerBackend interface --------------------------------------
    def market_day(self) -> int:
        return self._day

    def step_market(self) -> None:
        # Live path does not need to advance an internal clock; the exchange
        # clock is the source of truth. No-op keeps the orchestrator uniform.
        return None

    def get_account(self) -> Account:
        out = self._run(build_account_command(self.bin))
        return Account(
            equity=float(out.get("equity", self.starting)),
            cash=float(out.get("cash", self.starting)),
            buying_power=float(out.get("buying_power", self.starting)),
            day_pnl=0.0,
            total_pnl=float(out.get("equity", self.starting)) - self.starting,
            positions_count=len(self._positions),
        )

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def submit_order(self, order: Order) -> SubmitResult:
        cmd = build_mleg_command(self.bin, order)
        try:
            out = self._run(cmd)
        except RuntimeError as e:
            return SubmitResult(ok=False, message=str(e))
        pid = out.get("id") or out.get("client_order_id") or "alpaca_order"
        # We record a Position locally so the monitor has a consistent view.
        pos = Position(
            id=pid, strategy=order.strategy, root=order.root, legs=order.legs,
            qty=order.qty, opened_day=self._day,
            expiry_day=max((lg.expiry_day for lg in order.legs), default=0),
            entry_credit=order.net_credit,
        )
        self._positions[pid] = pos
        return SubmitResult(ok=True, order_id=pid,
                            message=f"submitted {order.strategy}", raw=out)

    def close_position(self, position_id: str, reason: str = "") -> SubmitResult:
        pos = self._positions.get(position_id)
        if not pos:
            return SubmitResult(ok=False, message="no such position")
        try:
            out = self._run(build_close_command(self.bin, pos))
        except RuntimeError as e:
            return SubmitResult(ok=False, message=str(e))
        pos.status = "closed"
        pos.exit_reason = reason or "manual"
        return SubmitResult(ok=True, order_id=position_id,
                            message="closed", raw=out)

    def cancel_all(self) -> SubmitResult:
        try:
            self._run(build_cancel_all_command(self.bin))
        except RuntimeError as e:
            return SubmitResult(ok=False, message=str(e))
        for p in self._positions.values():
            p.status = "closed"
        return SubmitResult(ok=True, message="canceled all")
