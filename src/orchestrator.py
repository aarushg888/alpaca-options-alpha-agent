"""Orchestrator: the autonomous agent loop.

Decides backend (simulated vs Alpaca CLI) from config, then runs:
  for each bar:
    1. advance market (sim) / read clock (live)
    2. mark positions (sim) / refresh (live)
    3. monitor exits (TP/SL/compaction)
    4. drawdown check
    5. propose new positions (signal -> candidates -> risk gates)
    6. execute approved orders
    7. record equity curve

This same loop powers both the live `run.py` (week-long competition) and the
`backtest.py` (historical sim) so behavior is identical in both.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.agent.decision_engine import propose
from src.agent.executor import Executor
from src.agent.position_monitor import monitor
from src.broker.alpaca_cli import AlpacaCliBroker
from src.broker.simulated_broker import SimulatedBroker
from src.config import Config
from src.market_sim import MarketSimulator
from src.market.live_market import LiveMarket
from src.risk.gates import RiskGates

logger = logging.getLogger("orchestrator")


@dataclass
class RunResult:
    backend: str
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    final_equity: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    n_trades: int = 0
    log: list[str] = field(default_factory=list)


def build_backend(config: Config, market: Optional[object] = None):
    """Return (broker, market_view).

    market_view is whichever object provides the strategy interface
    (MarketSimulator in backtest, LiveMarket against real Alpaca data live).
    """
    if config.backend == "alpaca":
        return AlpacaCliBroker(config), (market or LiveMarket(config))
    market = market or MarketSimulator(config)
    return SimulatedBroker(config, market), market


def run(config: Config, steps: Optional[int] = None,
        market: Optional[object] = None,
        exec_log: Optional[str] = None,
        only_when_open: bool = False) -> RunResult:
    steps = steps if steps is not None else config.sim_steps
    broker, market = build_backend(config, market)
    risk = RiskGates(config)
    executor = Executor(broker, log_path=exec_log)
    peak = config.starting_balance

    result = RunResult(backend=config.backend)
    if market is None:
        # Live mode: steps loop bounded by wall-clock in run.py
        steps = steps or 1

    for step in range(steps):
        if hasattr(market, "step"):
            market.step()
        broker.step_market()
        acct = broker.get_account()

        # 3. Monitor exits
        actions = monitor(broker)
        for a in actions:
            result.log.append(f"step {step}: close {a.position_id} {a.reason} "
                              f"{'OK' if a.result.ok else 'FAIL'}")

        # 4. Drawdown halt
        halt, reason = risk.drawdown_halt(acct, peak)
        if halt:
            result.log.append(f"step {step}: DRAWDOWN HALT {reason}")
            # Halts new entries; existing positions still monitored.

        # 5/6. Propose + execute (skip new entries if halted / market closed)
        # Live mode uses a real Alpaca market-data adapter (LiveMarket); the
        # backtest uses the local simulator. Both expose the same interface the
        # strategy engine needs. With only_when_open, new entries are gated to
        # market hours (monitoring of existing positions always runs).
        market_closed = only_when_open and hasattr(broker, "market_open") \
            and not broker.market_open()
        if (not halt and not market_closed
                and len(broker.get_positions()) < config.max_positions
                and market is not None):
            proposals = propose(broker, market, config, risk)
            taken = 0
            for prop in proposals:
                if taken >= (config.target_positions - len(broker.get_positions())):
                    break
                res = executor.submit(prop)
                if res.ok:
                    result.trades.append({
                        "step": step, "root": prop.order.root,
                        "strategy": prop.order.strategy,
                        "qty": max(1, min(prop.order.qty, prop.gate_max_qty)),
                        "net_credit": round(prop.order.net_credit, 4),
                        "rationale": prop.rationale,
                    })
                    taken += 1

        acct = broker.get_account()
        eq = acct.equity
        peak = max(peak, eq)
        result.equity_curve.append(round(eq, 2))

    final = broker.get_account()
    result.final_equity = final.equity
    result.total_pnl = final.equity - config.starting_balance
    result.n_trades = len(result.trades)
    # max drawdown from equity curve
    peakq = config.starting_balance
    mdd = 0.0
    for eq in result.equity_curve:
        peakq = max(peakq, eq)
        mdd = max(mdd, (peakq - eq) / peakq)
    result.max_drawdown = mdd
    return result


def summarize(result: RunResult) -> dict:
    return {
        "backend": result.backend,
        "final_equity": round(result.final_equity, 2),
        "total_pnl": round(result.total_pnl, 2),
        "return_pct": round(100 * result.total_pnl / 100000.0, 2) if result.equity_curve else 0,
        "n_trades": result.n_trades,
        "max_drawdown_pct": round(100 * result.max_drawdown, 2),
        "equity_curve_tail": result.equity_curve[-10:],
    }
