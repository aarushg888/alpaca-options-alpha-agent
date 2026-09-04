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
import time
from datetime import date
from typing import Optional

from src.broker.base import (
    Account, BrokerBackend, Leg, Order, Position, SubmitResult,
)


def _leg_to_cli(leg: Leg) -> dict:
    return {"symbol": leg.symbol, "side": leg.side, "ratio_qty": str(leg.qty)}


def build_option_chain_command(bin_path: str, underlying: str,
                                exp_gte: str, exp_lte: str) -> list[str]:
    """Fetch the real option chain via the Alpaca CLI (public market data)."""
    return [bin_path, "option", "contracts",
            "--underlying-symbols", underlying,
            "--expiration-date-gte", exp_gte,
            "--expiration-date-lte", exp_lte,
            "--limit", "10000"]


def build_mleg_command(bin_path: str, order: Order) -> list[str]:
    """Construct the `alpaca order submit` CLI args for a multi-leg spread.

    Uses a limit order when `order.limit_price` is set (required for options
    outside market hours, and safer generally). Falls back to market.
    """
    legs = [_leg_to_cli(lg) for lg in order.legs]
    cmd = [
        bin_path, "order", "submit",
        "--order-class", "mleg",
        "--time-in-force", order.time_in_force,
        "--qty", str(order.qty),
        "--legs", json.dumps(legs),
        "--client-order-id", new_client_order_id(order.strategy, order.root),
    ]
    if order.limit_price is not None:
        cmd += ["--type", "limit", "--limit-price", f"{order.limit_price:.2f}"]
    else:
        cmd += ["--type", "market"]
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


def build_positions_command(bin_path: str) -> list[str]:
    return [bin_path, "position", "list"]


def parse_occ(symbol: str) -> tuple[str, str, str, float]:
    """Split an OCC option symbol into (root, expiry YYYY-MM-DD, C/P, strike).

    e.g. SPY260930C00808000 -> ("SPY", "2026-09-30", "C", 808.0).
    """
    s = symbol.strip()
    m_len = len(s)
    strike = float(s[m_len - 8:]) / 1000.0
    cp = s[m_len - 9]
    yymmdd = s[m_len - 15:m_len - 9]
    root = s[:m_len - 15].strip()
    exp = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    return root, exp, cp, strike


_order_seq = 0


def new_client_order_id(strategy: str, root: str) -> str:
    """Unique client order id per submitted order.

    The previous `{strategy}-{root}` format collided with Alpaca's uniqueness
    requirement whenever the same spread was re-entered on a later tick
    (HTTP 422 client_order_id must be unique).
    """
    global _order_seq
    _order_seq += 1
    return f"{strategy}-{root}-{int(time.time())}-{_order_seq}"


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

    def market_open(self) -> bool:
        """True if the US equity options session is open right now."""
        try:
            out = self._run([self.bin, "clock"])
            return bool(out.get("is_open", False))
        except Exception:
            return False

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

    def sync_positions(self) -> int:
        """Rebuild the local ledger from Alpaca's real open positions.

        Each `--once` tick runs in a fresh process with an empty ledger, so
        without this the risk gates (count/aggregate ceilings, per-root
        dedup) and the TP/SL monitor are blind to the live book and every
        tick keeps adding spreads. Legs are grouped by (root, expiry) into
        spread Positions; entry credit is reconstructed from average entry
        prices (short legs +, long legs -) and unrealized P&L is taken from
        Alpaca's per-leg `unrealized_pl`. On CLI failure the existing ledger
        is left untouched and 0 is returned.
        """
        try:
            out = self._run(build_positions_command(self.bin))
        except Exception:
            return 0
        legs = out if isinstance(out, list) else out.get("positions", out.get("position", []))
        if isinstance(legs, dict):
            legs = [legs]
        groups: dict[tuple[str, str], list[dict]] = {}
        for entry in legs:
            sym = str(entry.get("symbol", ""))
            if not sym:
                continue
            try:
                root, exp, _cp, _strike = parse_occ(sym)
            except Exception:
                continue
            groups.setdefault((root, exp), []).append(entry)
        today = date.today()
        synced: dict[str, Position] = {}
        for (root, exp), entries in groups.items():
            pos_legs: list[Leg] = []
            credit = 0.0
            upl = 0.0
            for e in entries:
                try:
                    root_e, _exp, cp, strike = parse_occ(str(e.get("symbol")))
                except Exception:
                    continue
                qty = float(e.get("qty", 0) or 0)
                avg = float(e.get("avg_entry_price", 0) or 0)
                side = "sell" if qty < 0 else "buy"
                credit += (abs(qty) * avg) if qty < 0 else -(abs(qty) * avg)
                try:
                    upl += float(e.get("unrealized_pl", 0) or 0)
                except (TypeError, ValueError):
                    pass
                pos_legs.append(Leg(
                    symbol=str(e.get("symbol")), side=side, root=root_e,
                    qty=int(abs(qty)) or 1, asset_class="option",
                    type="call" if cp == "C" else "put", strike=strike,
                    expiry_day=0, entry_price=avg,
                ))
            if not pos_legs:
                continue
            try:
                y, m, d = int(exp[0:4]), int(exp[5:7]), int(exp[8:10])
                dte = (date(y, m, d) - today).days
            except Exception:
                dte = 0
            pid = f"live-{root}-{exp}"
            synced[pid] = Position(
                id=pid, strategy="live_spread", root=root, legs=pos_legs,
                qty=1, opened_day=0, expiry_day=max(0, dte),
                entry_credit=credit, unrealized_pnl=upl,
            )
        self._positions = synced
        return len(synced)

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
