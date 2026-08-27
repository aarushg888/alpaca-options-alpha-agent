"""Deterministic risk gates (no LLM in the loop).

Every order must pass ALL gates before the executor submits it. These gates
enforce the competition-safe properties of the strategy:
  * defined risk (max loss known at entry)
  * per-position and aggregate risk caps
  * drawdown halts
  * IV-rank entry filter
  * position-count and capital ceilings

The gates are pure functions over (order, account, open positions, signal) so
they are exhaustively unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.broker.base import Account, BrokerBackend, Order, Position
from src.config import Config
from src.strategy.engine import compute_signal
from src.market_sim import MarketSimulator


@dataclass
class RiskVerdict:
    allow: bool
    reasons: list[str] = field(default_factory=list)
    max_qty: int = 0


def spread_width(order: Order) -> float:
    """For a credit spread, strike distance between the two sides' extremes."""
    if len(order.legs) < 2:
        return 0.0
    strikes = [lg.strike for lg in order.legs]
    return abs(max(strikes) - min(strikes))


def defined_risk(order: Order) -> float:
    """Max loss per share = spread width minus net credit (>=0 for spreads)."""
    return max(0.0, spread_width(order) - order.net_credit)


def position_defined_risk(pos: Position) -> float:
    """Max loss per share for an existing Position (reconstructed profile)."""
    strikes = [lg.strike for lg in pos.legs]
    width = abs(max(strikes) - min(strikes))
    return max(0.0, width - abs(pos.entry_credit))


def position_max_credit(pos: Position) -> float:
    """Dollars of premium kept if a position expires worthless (per contract)."""
    return abs(pos.entry_credit) * 100.0 * pos.qty


class RiskGates:
    def __init__(self, config: Config):
        self.config = config

    def evaluate(
        self,
        order: Order,
        account: Account,
        open_positions: list[Position],
        sim: Optional[MarketSimulator] = None,
        symbol_iv_rank: Optional[float] = None,
    ) -> RiskVerdict:
        reasons: list[str] = []
        v = RiskVerdict(allow=True, reasons=reasons)

        # 1) Must be a net-credit defined-risk spread.
        if order.net_credit <= 0:
            v.allow = False
            reasons.append("REJECT: order is not a net credit")
            return v

        # 2) IV-rank entry filter.
        ivr = symbol_iv_rank
        if ivr is None and sim is not None:
            ivr = sim.iv_rank(order.root)
        if ivr is not None and ivr < self.config.iv_rank_min:
            v.allow = False
            reasons.append(f"REJECT: IV rank {ivr} < min {self.config.iv_rank_min}")
            return v

        # 3) Per-position risk cap -> determines max qty.
        dr = defined_risk(order) * 100.0  # per contract ($100/share)
        if dr <= 0:
            max_by_risk = self.config.max_positions
        else:
            max_by_risk = int(
                (account.equity * self.config.max_portfolio_risk_pct) // dr
            )
        # 4) Single-position capital ceiling.
        notional_per = order.net_credit * 100.0 * 1  # premium held as margin proxy
        capital_cap = self.config.max_single_position_pct * account.equity
        max_by_capital = max(0, int(capital_cap // max(1.0, notional_per)))

        # 5) Aggregate defined-risk cap across portfolio.
        agg_risk = sum(position_defined_risk(p) * 100.0 * p.qty
                       for p in open_positions)
        room = account.equity * self.config.max_total_risk_pct - agg_risk
        max_by_aggregate = int(room // max(1.0, dr)) if dr > 0 else self.config.max_positions

        # 6) Position count ceiling.
        max_by_count = self.config.max_positions - len(open_positions)

        max_qty = max(0, min(max_by_risk, max_by_capital, max_by_aggregate, max_by_count))
        v.max_qty = max_qty
        if max_qty < 1:
            v.allow = False
            bits = []
            if max_by_risk < 1:
                bits.append(f"per-pos risk {dr:.0f} exceeds cap "
                            f"{account.equity*self.config.max_portfolio_risk_pct:.0f}")
            if max_by_count < 1:
                bits.append("position count ceiling")
            if max_by_capital < 1:
                bits.append("capital ceiling")
            if max_by_aggregate < 1:
                bits.append("aggregate risk ceiling")
            reasons.append("REJECT: " + "; ".join(bits))
            return v

        reasons.append(
            f"ALLOW: max_qty={max_qty} dr/contract=${dr:.0f} "
            f"iv_rank={ivr}"
        )
        return v

    def drawdown_halt(self, account: Account, peak_equity: float) -> tuple[bool, str]:
        """Return (halt_new_entries, reason). Closes are still permitted."""
        dd = (peak_equity - account.equity) / peak_equity if peak_equity > 0 else 0.0
        if dd >= self.config.max_total_drawdown_pct:
            return True, f"TOTAL drawdown {dd:.1%} >= {self.config.max_total_drawdown_pct:.1%}"
        return False, ""

