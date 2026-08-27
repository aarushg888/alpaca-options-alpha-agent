"""Protocol describing the market view the strategy engine consumes.

Both the local `MarketSimulator` (backtest) and the live `LiveMarket` (real
Alpaca data) implement this duck-typed interface, so the strategy/risk/executor
code is identical in both modes.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MarketView(Protocol):
    day: int

    def get_underlying(self, symbol: str) -> float: ...
    def iv_rank(self, symbol: str) -> float: ...
    def price_option(self, symbol: str, otype: str, strike: float,
                     dte_days: int, mid: bool = ...) -> float: ...
    def get_chain(self, symbol: str, dte_min: int | None = ...,
                  dte_max: int | None = ...) -> list: ...
    def step(self) -> None: ...
