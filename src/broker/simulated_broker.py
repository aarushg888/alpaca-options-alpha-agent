"""Simulated broker backend (correct, single-path accounting).

Bookkeeping model (paper-sim, but economically faithful):
  * cash stays at the starting balance (margin is notional; we only track P&L).
  * equity = cash + realized_pnl + sum(unrealized_pnl of open positions)
  * realized_pnl accrues only when a position is closed/expires.
  * Each open position is marked every step via the SAME `_close_pnl` used at
    close, so marking and settlement are identical and there is no double count.

Net credit at open (per share) = sum(side_sign * entry_price).
Close/settle P&L (per share)   = entry_credit - sum(side_sign * close_value),
where close_value is the option mid BEFORE expiry and the intrinsic value AT
expiry. For a credit spread that expires in-the-money this yields the defined
max loss (wing - credit); for out-of-the-money it yields the kept premium.
"""
from __future__ import annotations

import uuid
from typing import Optional

from src.broker.base import (
    Account, BrokerBackend, Leg, Order, Position, SubmitResult,
)
from src.market_sim import MarketSimulator
from src.risk.gates import position_defined_risk, position_max_credit


class SimulatedBroker(BrokerBackend):
    def __init__(self, config, simulator: MarketSimulator, start_balance: float | None = None):
        self.config = config
        self.sim = simulator
        self.starting = start_balance if start_balance is not None else config.starting_balance
        self.cash = self.starting
        self.realized = 0.0
        self.positions: dict[str, Position] = {}
        self.peak_equity = self.starting

    # ---- account / state ----------------------------------------------
    def _open_unrealized(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values() if p.status == "open")

    def get_account(self) -> Account:
        equity = self.cash + self.realized + self._open_unrealized()
        return Account(
            equity=equity,
            cash=self.cash,
            buying_power=equity,
            day_pnl=0.0,
            total_pnl=equity - self.starting,
            positions_count=len([p for p in self.positions.values() if p.status == "open"]),
        )

    def get_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.status == "open"]

    def market_day(self) -> int:
        return self.sim.day

    def step_market(self) -> None:
        self._mark_positions()

    # ---- per-leg valuation --------------------------------------------
    def _leg_value(self, lg: Leg, day: int, at_expiry: bool) -> float:
        if at_expiry or day >= lg.expiry_day:
            spot = self.sim.get_underlying(lg.root)
            if lg.type == "call":
                return max(0.0, spot - lg.strike)
            return max(0.0, lg.strike - spot)
        return self.sim.price_option(lg.root, lg.type, lg.strike,
                                     max(1, lg.expiry_day - day), mid=True)

    def _close_pnl(self, pos: Position, day: int, at_expiry: bool = False) -> float:
        """P&L in dollars of closing/settling the position now."""
        unwind = 0.0
        for lg in pos.legs:
            v = self._leg_value(lg, day, at_expiry)
            s = 1.0 if lg.side == "sell" else -1.0
            unwind += s * v
        # entry_credit is per share; unwind is per share. Per-contract=*100*qty.
        return (pos.entry_credit - unwind) * 100.0 * pos.qty

    # ---- marking / lifecycle ------------------------------------------
    def _realize(self, pos: Position, pnl: float, day: int, reason: str) -> None:
        self.realized += pnl
        pos.status = "closed"
        pos.realized_pnl = pnl
        pos.exit_day = day
        pos.exit_reason = reason

    def _mark_positions(self) -> None:
        day = self.sim.day
        for pos in list(self.positions.values()):
            if pos.status != "open":
                continue
            max_credit = position_max_credit(pos)          # dollars kept if untouched
            dr = position_defined_risk(pos) * 100.0 * pos.qty  # max loss in dollars
            pnl = self._close_pnl(pos, day)
            pos.current_close_cost = (pos.entry_credit - pnl / (100.0 * pos.qty)) if pos.qty else 0.0
            pos.unrealized_pnl = pnl

            if day >= pos.expiry_day:
                self._realize(pos, pnl, day, "expiry")
            elif pnl >= 0.5 * max_credit and max_credit > 1e-9:
                self._realize(pos, pnl, day, "take_profit")
            elif dr > 1e-9 and pnl <= -dr:
                self._realize(pos, pnl, day, "stop_loss")
            elif (day - pos.opened_day) >= 21 and pnl > 0:
                self._realize(pos, pnl, day, "early_compaction")

        eq = self.get_account().equity
        self.peak_equity = max(self.peak_equity, eq)

    # ---- execution ----------------------------------------------------
    def submit_order(self, order: Order) -> SubmitResult:
        if order.order_class != "mleg":
            return SubmitResult(ok=False, message="sim supports mleg option spreads only")
        credit = order.net_credit
        if credit <= 0:
            return SubmitResult(ok=False, message="order has no net credit")
        pid = f"pos_{uuid.uuid4().hex[:8]}"
        pos = Position(
            id=pid,
            strategy=order.strategy,
            root=order.root,
            legs=order.legs,
            qty=order.qty,
            opened_day=self.sim.day,
            expiry_day=max(lg.expiry_day for lg in order.legs),
            entry_credit=credit,
        )
        self.positions[pid] = pos
        return SubmitResult(ok=True, order_id=pid, message=f"opened {order.strategy} {pid}")

    def close_position(self, position_id: str, reason: str = "") -> SubmitResult:
        pos = self.positions.get(position_id)
        if not pos or pos.status != "open":
            return SubmitResult(ok=False, message="no open position")
        day = self.sim.day
        pnl = self._close_pnl(pos, day)
        self._realize(pos, pnl, day, reason or "manual")
        return SubmitResult(ok=True, order_id=position_id,
                            message=f"closed {pos.strategy} pnl={pnl:.2f}")

    def cancel_all(self) -> SubmitResult:
        for pid in list(self.positions.keys()):
            p = self.positions[pid]
            if p.status == "open":
                self.close_position(pid, reason="cancel_all")
        return SubmitResult(ok=True, message="canceled all")
