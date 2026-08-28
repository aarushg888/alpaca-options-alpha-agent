"""Broker abstraction: a single interface for both the simulated local
execution backend and the live Alpaca CLI backend.

The decision engine and position monitor only ever talk to `BrokerBackend`,
so switching from simulated to live trading is a one-line config change
(no matter what: it's driven by the presence of Alpaca credentials).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Leg:
    symbol: str           # OCC symbol (or root for single-leg equity)
    side: str             # "buy" | "sell"
    root: str = ""        # underlying root symbol (for sim pricing)
    qty: int = 1
    asset_class: str = "option"  # "option" | "stock"
    type: str = "call"    # option type (call/put) when asset_class=option
    strike: float = 0.0
    expiry_day: int = 0
    entry_price: float = 0.0  # per-share mid at open (sim accounting)


@dataclass
class Order:
    order_class: str      # "mleg" | "simple"
    strategy: str         # "iron_condor" | "put_credit_spread" | ...
    root: str
    legs: list[Leg]
    qty: int = 1
    time_in_force: str = "day"
    limit_price: Optional[float] = None  # net spread price; None => market
    note: str = ""

    @property
    def net_credit(self) -> float:
        """Premium received at open (per share), positive = net credit."""
        if not self.legs:
            return 0.0
        # First leg determines sign convention: for a credit spread we SELL the
        # body. Use sum of signed mids (short=+credit, long=-debit).
        total = 0.0
        for lg in self.legs:
            s = 1.0 if lg.side == "sell" else -1.0
            total += s * lg.entry_price
        return total


@dataclass
class Position:
    id: str
    strategy: str
    root: str
    legs: list[Leg]
    qty: int
    opened_day: int
    expiry_day: int
    entry_credit: float          # per share premium received at open
    current_close_cost: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: Optional[float] = None
    status: str = "open"         # "open" | "closed"
    exit_day: Optional[int] = None
    exit_reason: str = ""

    @property
    def dte(self) -> int:
        return self.expiry_day


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    day_pnl: float
    total_pnl: float
    positions_count: int
    currency: str = "USD"


@dataclass
class SubmitResult:
    ok: bool
    order_id: Optional[str] = None
    message: str = ""
    raw: Optional[dict] = None


class BrokerBackend(ABC):
    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> list[Position]: ...

    @abstractmethod
    def submit_order(self, order: Order) -> SubmitResult: ...

    @abstractmethod
    def close_position(self, position_id: str, reason: str = "") -> SubmitResult: ...

    @abstractmethod
    def cancel_all(self) -> SubmitResult: ...

    @abstractmethod
    def market_day(self) -> int: ...   # current sim/exchange day index

    @abstractmethod
    def step_market(self) -> None: ...  # advance one bar (sim only; noop live)

    def market_open(self) -> bool:
        """Whether the trading session is open (live backends override)."""
        return True
