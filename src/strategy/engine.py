"""Strategy engine: from market state -> candidate option spreads.

Design goals:
  * Every strategy is DEFINED-RISK (credit spreads / iron condors). Max loss is
    known at entry = width of spread minus net credit. This satisfies the
    hackathon's "options trading" requirement with a risk-bounded profile.
  * Strategy selection is signal-driven (IV rank + regime) but deterministic,
    so the agent is explainable and the risk gates downstream are meaningful.
  * The engine emits `Order` objects (broker-agnostic) the executor submits.

Spreads implemented:
  * put_credit_spread     — bullish / neutral, sell OTM put, buy lower put
  * call_credit_spread    — bearish / neutral, sell OTM call, buy higher call
  * iron_condor           — combine the two, market-neutral income
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from src.broker.base import Leg, Order
from src.config import Config
from src.market_sim import MarketSimulator, make_occ_symbol, _fmt_day


@dataclass
class Signal:
    symbol: str
    iv_rank: float
    price: float
    regime: str            # "low_iv" | "normal_iv" | "high_iv"
    bias: str              # "neutral" | "bullish" | "bearish"
    score: float           # 0..1 attractiveness for a new position


def classify_iv_rank(iv_rank: float) -> str:
    if iv_rank < 35:
        return "low_iv"
    if iv_rank > 70:
        return "high_iv"
    return "normal_iv"


def compute_signal(symbol: str, sim: MarketSimulator, config: Config) -> Signal:
    iv_rank = sim.iv_rank(symbol)
    price = sim.get_underlying(symbol)
    regime = classify_iv_rank(iv_rank)
    # Bias heuristic: high IV rank -> expect mean reversion (don't chase);
    # neutral income works across regimes. We stay market-neutral-biased by
    # default and only lean directional on extreme IV rank.
    if iv_rank > 80:
        bias = "neutral"   # fade extremes, stay balanced
    elif iv_rank < 25:
        bias = "bullish"    # cheap premium -> mild directional
    else:
        bias = "neutral"
    # Attractiveness rises with IV rank (more premium), capped.
    score = max(0.0, min(1.0, (iv_rank - 20) / 80.0))
    return Signal(symbol=symbol, iv_rank=iv_rank, price=price,
                  regime=regime, bias=bias, score=score)


def _expiry_day(sim: MarketSimulator, config: Config) -> tuple[int, str]:
    eday = sim.day + (config.min_dte + config.max_dte) // 2
    # round to a Friday-ish weekly (7-multiple)
    eday = sim.day + (((config.min_dte + config.max_dte) // 2) // 7) * 7
    return eday, _fmt_day(eday)


def _nearest_strike(spot: float, offset_pct: float) -> float:
    # Strike grid step ~2% of spot.
    step = max(1.0, round(spot * 0.02, 2))
    return round(spot * (1 + offset_pct) / step) * step


def build_put_credit_spread(symbol: str, sim: MarketSimulator, config: Config,
                            wing: float = 0.04) -> Optional[Order]:
    spot = sim.get_underlying(symbol)
    eday, eyyyymmdd = _expiry_day(sim, config)
    sell_strike = _nearest_strike(spot, -wing)      # OTM put sold
    buy_strike = _nearest_strike(spot, -2 * wing)   # lower put bought
    sell_iv = sim.price_option(symbol, "put", sell_strike, eday - sim.day)
    buy_iv = sim.price_option(symbol, "put", buy_strike, eday - sim.day)
    sell_px = sim.price_option(symbol, "put", sell_strike, eday - sim.day, mid=True)
    buy_px = sim.price_option(symbol, "put", buy_strike, eday - sim.day, mid=True)
    net = sell_px - buy_px
    if net < config.min_credit_quality_spread:
        return None
    legs = [
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "put", sell_strike),
            side="sell", root=symbol, asset_class="option", type="put",
            strike=sell_strike, expiry_day=eday, entry_price=sell_px),
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "put", buy_strike),
            side="buy", root=symbol, asset_class="option", type="put",
            strike=buy_strike, expiry_day=eday, entry_price=buy_px),
    ]
    return Order(order_class="mleg", strategy="put_credit_spread",
                 root=symbol, legs=legs, qty=1,
                 note=f"sell {sell_strike}P / buy {buy_strike}P net {net:.2f}")


def build_call_credit_spread(symbol: str, sim: MarketSimulator, config: Config,
                             wing: float = 0.04) -> Optional[Order]:
    spot = sim.get_underlying(symbol)
    eday, eyyyymmdd = _expiry_day(sim, config)
    sell_strike = _nearest_strike(spot, wing)       # OTM call sold
    buy_strike = _nearest_strike(spot, 2 * wing)    # higher call bought
    sell_px = sim.price_option(symbol, "call", sell_strike, eday - sim.day, mid=True)
    buy_px = sim.price_option(symbol, "call", buy_strike, eday - sim.day, mid=True)
    net = sell_px - buy_px
    if net < config.min_credit_quality_spread:
        return None
    legs = [
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "call", sell_strike),
            side="sell", root=symbol, asset_class="option", type="call",
            strike=sell_strike, expiry_day=eday, entry_price=sell_px),
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "call", buy_strike),
            side="buy", root=symbol, asset_class="option", type="call",
            strike=buy_strike, expiry_day=eday, entry_price=buy_px),
    ]
    return Order(order_class="mleg", strategy="call_credit_spread",
                 root=symbol, legs=legs, qty=1,
                 note=f"sell {sell_strike}C / buy {buy_strike}C net {net:.2f}")


def build_iron_condor(symbol: str, sim: MarketSimulator, config: Config,
                      wing: float = 0.05) -> Optional[Order]:
    spot = sim.get_underlying(symbol)
    eday, eyyyymmdd = _expiry_day(sim, config)
    # Put side
    sell_p = _nearest_strike(spot, -wing)
    buy_p = _nearest_strike(spot, -2 * wing)
    # Call side
    sell_c = _nearest_strike(spot, wing)
    buy_c = _nearest_strike(spot, 2 * wing)
    p_sell = sim.price_option(symbol, "put", sell_p, eday - sim.day, mid=True)
    p_buy = sim.price_option(symbol, "put", buy_p, eday - sim.day, mid=True)
    c_sell = sim.price_option(symbol, "call", sell_c, eday - sim.day, mid=True)
    c_buy = sim.price_option(symbol, "call", buy_c, eday - sim.day, mid=True)
    net = (p_sell - p_buy) + (c_sell - c_buy)
    if net < config.min_credit_quality_spread * 1.5:
        return None
    legs = [
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "put", sell_p),
            side="sell", root=symbol, asset_class="option", type="put",
            strike=sell_p, expiry_day=eday, entry_price=p_sell),
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "put", buy_p),
            side="buy", root=symbol, asset_class="option", type="put",
            strike=buy_p, expiry_day=eday, entry_price=p_buy),
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "call", sell_c),
            side="sell", root=symbol, asset_class="option", type="call",
            strike=sell_c, expiry_day=eday, entry_price=c_sell),
        Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, "call", buy_c),
            side="buy", root=symbol, asset_class="option", type="call",
            strike=buy_c, expiry_day=eday, entry_price=c_buy),
    ]
    return Order(order_class="mleg", strategy="iron_condor",
                 root=symbol, legs=legs, qty=1,
                 note=f"IC {sell_p}P/{buy_p}P/{sell_c}C/{buy_c}C net {net:.2f}")


def wing_for_ivrank(iv_rank: float, base: float = 0.05, min_wing: float = 0.03,
                    max_wing: float = 0.07) -> float:
    """Adaptive wing width: tighten spreads when IV rank is extreme.

    When implied vol is very high, fat tails are more likely, so we shrink the
    strike distance (max loss) to keep defined risk small. When IV rank is
    moderate we open wings a bit wider for more credit. This is a risk-aware
    sizing rule, not an optimization fit to seed data.
    """
    # iv_rank 0..100. Extreme (high or low) => smaller wings.
    extremity = abs(iv_rank - 50) / 50.0  # 0 at 50, 1 at 0 or 100
    wing = base - (base - min_wing) * extremity
    return round(min(max_wing, max(min_wing, wing)), 4)


def generate_candidates(symbol: str, sim: MarketSimulator, config: Config,
                        signals: dict[str, Signal] | None = None) -> list[Order]:
    """Produce ranked candidate orders for a symbol given its signal."""
    sig = signals.get(symbol) if signals else None
    if sig is None:
        sig = compute_signal(symbol, sim, config)
    wing = wing_for_ivrank(sig.iv_rank)
    candidates: list[Order] = []
    # Iron condor is the market-neutral core; directional credit spreads are
    # added when bias leans. Order reflects preference.
    ic = build_iron_condor(symbol, sim, config, wing=wing)
    if ic:
        candidates.append(ic)
    if sig.bias in ("bullish", "neutral"):
        pcs = build_put_credit_spread(symbol, sim, config, wing=wing)
        if pcs:
            candidates.append(pcs)
    if sig.bias in ("bearish", "neutral"):
        ccs = build_call_credit_spread(symbol, sim, config, wing=wing)
        if ccs:
            candidates.append(ccs)
    # Rank by net credit (higher premium = more income / better risk-reward).
    candidates.sort(key=lambda o: o.net_credit, reverse=True)
    return candidates
