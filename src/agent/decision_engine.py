"""Decision engine: scans the universe, scores signals, and proposes orders.

This is the 'AI logic' layer. It is deliberately deterministic and explainable:
a critic-style scoring pass ranks opportunities, risk gates then filter them,
and the executor only submits what passes. (In the hackathon's multi-agent
spirit, you can replace `propose` with an LLM critic — the interface is stable.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.broker.base import BrokerBackend, Order, Position
from src.config import Config
from src.market_sim import MarketSimulator
from src.risk.gates import RiskGates
from src.strategy.engine import compute_signal, generate_candidates


@dataclass
class Proposal:
    order: Order
    signal_score: float
    gate_max_qty: int
    rationale: str


def propose(broker: BrokerBackend, sim: MarketSimulator, config: Config,
            risk: RiskGates) -> list[Proposal]:
    acct = broker.get_account()
    open_positions = broker.get_positions()
    proposals: list[Proposal] = []

    for u in config.universe:
        symbol = u["symbol"]
        sig = compute_signal(symbol, sim, config)
        if sig.iv_rank < config.iv_rank_min:
            continue
        candidates = generate_candidates(symbol, sim, config, {symbol: sig})
        for cand in candidates:
            verdict = risk.evaluate(
                cand, acct, open_positions, sim=sim, symbol_iv_rank=sig.iv_rank
            )
            if verdict.allow and verdict.max_qty >= 1:
                # avoid duplicate root already open
                if any(p.root == symbol and p.status == "open" for p in open_positions):
                    continue
                proposals.append(Proposal(
                    order=cand, signal_score=sig.score,
                    gate_max_qty=verdict.max_qty,
                    rationale=(f"{cand.strategy} {symbol} iv_rank={sig.iv_rank} "
                               f"net={cand.net_credit:.2f} qty<= {verdict.max_qty}"),
                ))
    # Rank: prefer higher signal score then higher premium.
    proposals.sort(key=lambda p: (p.signal_score, p.order.net_credit), reverse=True)
    return proposals
