"""Position monitor: pure price-based exits (no LLM).

Runs every 'bar'. For each open position it checks:
  * Expiry (handled by broker settle)
  * Take-profit: close when >= 50% of max credit captured
  * Stop-loss: close when loss >= defined risk (max loss)
  * Time-based compaction: take profits early (T+21) to free risk budget

Returns a list of close actions taken (for logging/audit).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.broker.base import BrokerBackend, Position, SubmitResult


@dataclass
class MonitorAction:
    position_id: str
    reason: str
    result: SubmitResult


def _defined_risk(pos: Position) -> float:
    strikes = [lg.strike for lg in pos.legs]
    width = abs(max(strikes) - min(strikes))
    return max(0.0, width - abs(pos.entry_credit)) * 100.0  # per contract


def monitor(broker: BrokerBackend, tp_pct: float = 0.5,
            sl_pct: float = 1.0, early_take_day: int = 21) -> list[MonitorAction]:
    actions: list[MonitorAction] = []
    for pos in broker.get_positions():
        if pos.status != "open":
            continue
        if pos.unrealized_pnl is None:
            continue
        dr = _defined_risk(pos)
        max_credit = abs(pos.entry_credit) * 100.0 * pos.qty
        # Take profit
        if pos.unrealized_pnl >= tp_pct * max_credit:
            res = broker.close_position(pos.id, reason="take_profit")
            actions.append(MonitorAction(pos.id, "take_profit", res))
            continue
        # Stop loss (reached defined max risk)
        if pos.unrealized_pnl <= -sl_pct * dr:
            res = broker.close_position(pos.id, reason="stop_loss")
            actions.append(MonitorAction(pos.id, "stop_loss", res))
            continue
        # Early compaction: once > early_take_day since open and in profit
        days_open = (broker.market_day() - pos.opened_day)
        if days_open >= early_take_day and pos.unrealized_pnl > 0:
            res = broker.close_position(pos.id, reason="early_compaction")
            actions.append(MonitorAction(pos.id, "early_compaction", res))
    return actions
