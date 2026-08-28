"""Executor: submits approved orders to the broker backend.

Thin, auditable layer. Every submission is logged with its risk rationale so
the agent's actions are fully explainable (required by the hackathon's
'Presentation & Execution' judging axis).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from src.agent.decision_engine import Proposal
from src.broker.base import BrokerBackend, SubmitResult

logger = logging.getLogger("executor")


class Executor:
    def __init__(self, broker: BrokerBackend, log_path: Optional[str] = None):
        self.broker = broker
        self.log_path = log_path

    def submit(self, proposal: Proposal) -> SubmitResult:
        order = proposal.order
        order.qty = max(1, min(order.qty, proposal.gate_max_qty))
        # Options: use a limit at the target net credit (we collect premium).
        # Limit orders are required outside market hours and avoid slippage.
        if order.limit_price is None and order.net_credit > 0:
            order.limit_price = round(order.net_credit, 2)
        res = self.broker.submit_order(order)
        entry = {
            "ok": res.ok,
            "order_id": res.order_id,
            "strategy": order.strategy,
            "root": order.root,
            "qty": order.qty,
            "net_credit": round(order.net_credit, 4),
            "note": order.note,
            "rationale": proposal.rationale,
            "message": res.message,
        }
        logger.info("EXEC %s", json.dumps(entry))
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        return res
