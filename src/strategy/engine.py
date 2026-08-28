"""Strategy engine: from market state -> candidate option spreads.

Design goals:
  * Every strategy is DEFINED-RISK (credit spreads / iron condors). Max loss is
    known at entry = width of spread minus net credit.
  * Strategy selection is signal-driven (IV rank + regime) but deterministic,
    so the agent is explainable and the risk gates downstream are meaningful.
  * The engine emits `Order` objects (broker-agnostic) the executor submits.
  * For LIVE trading the engine uses the REAL contract symbols/bid-ask from the
    market-data chain (so the OCC symbols actually exist on Alpaca). The local
    simulator path synthesizes symbols (valid for backtests).

Spreads implemented:
  * put_credit_spread     — bullish / neutral, sell OTM put, buy lower put
  * call_credit_spread    — bearish / neutral, sell OTM call, buy higher call
  * iron_condor           — combine the two, market-neutral income
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.broker.base import Leg, Order
from src.config import Config
from src.market.protocol import MarketView
from src.market_sim import make_occ_symbol, _fmt_day


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


def compute_signal(symbol: str, sim: MarketView, config: Config) -> Signal:
    iv_rank = sim.iv_rank(symbol)
    price = sim.get_underlying(symbol)
    regime = classify_iv_rank(iv_rank)
    if iv_rank > 80:
        bias = "neutral"
    elif iv_rank < 25:
        bias = "bullish"
    else:
        bias = "neutral"
    score = max(0.0, min(1.0, (iv_rank - 20) / 80.0))
    return Signal(symbol=symbol, iv_rank=iv_rank, price=price,
                  regime=regime, bias=bias, score=score)


def _nearest_strike(spot: float, offset_pct: float) -> float:
    step = max(1.0, round(spot * 0.02, 2))
    return round(spot * (1 + offset_pct) / step) * step


def _pick_leg(chain, otype: str, target_strike: float, dte: int):
    """Return a real Contract from the chain closest to (otype, strike, dte)."""
    best = None
    best_d = 1e9
    for c in chain:
        if c.type != otype:
            continue
        if abs(c.expiry_day - dte) > 5:
            continue
        d = abs(c.strike - target_strike)
        if d < best_d:
            best_d = d
            best = c
    return best


def _expiry_day(sim: MarketView, config: Config) -> int:
    """Target expiry (absolute sim day) = closest available expiration to the
    midpoint of the DTE window. For live data we must pick an expiration the
    chain actually contains (it only offers specific Fridays)."""
    target = sim.day + (config.min_dte + config.max_dte) // 2
    try:
        # collect distinct expirations present in the chain for any symbol
        exps: set[int] = set()
        for u in config.universe:
            chain = sim.get_chain(u["symbol"], dte_min=config.min_dte,
                                  dte_max=config.max_dte)
            for c in chain:
                exps.add(c.expiry_day)
        if exps:
            return min(exps, key=lambda e: abs(e - target))
    except Exception:
        pass
    return target


def _make_legs(sim: MarketView, symbol: str, otype: str, sell_strike: float,
               buy_strike: float, eday: int, config: Config):
    """Build the two legs, preferring real chain contracts when available."""
    chain = sim.get_chain(symbol, dte_min=max(1, eday - 5), dte_max=eday + 5)
    legs = []
    for side, strike in (("sell", sell_strike), ("buy", buy_strike)):
        real = _pick_leg(chain, otype, strike, eday)
        if real is not None:
            legs.append(Leg(symbol=real.symbol, side=side, root=symbol,
                            asset_class="option", type=otype,
                            strike=real.strike, expiry_day=real.expiry_day,
                            entry_price=real.mid))
        else:
            # Simulator fallback: synthesize OCC symbol.
            eyyyymmdd = _fmt_day(eday)
            legs.append(Leg(symbol=make_occ_symbol(symbol, eyyyymmdd, otype, strike),
                            side=side, root=symbol, asset_class="option", type=otype,
                            strike=strike, expiry_day=eday,
                            entry_price=sim.price_option(symbol, otype, strike,
                                                         max(1, eday - sim.day), mid=True)))
    return legs


def build_put_credit_spread(symbol: str, sim: MarketView, config: Config,
                            wing: float = 0.04) -> Optional[Order]:
    spot = sim.get_underlying(symbol)
    eday = _expiry_day(sim, config)
    sell_strike = _nearest_strike(spot, -wing)
    buy_strike = _nearest_strike(spot, -2 * wing)
    legs = _make_legs(sim, symbol, "put", sell_strike, buy_strike, eday, config)
    if len(legs) < 2 or any(l.entry_price <= 0 for l in legs):
        return None
    net = legs[0].entry_price - legs[1].entry_price
    if net < config.min_credit_quality_spread:
        return None
    return Order(order_class="mleg", strategy="put_credit_spread", root=symbol,
                 legs=legs, qty=1,
                 note=f"sell {legs[0].strike}P / buy {legs[1].strike}P net {net:.2f}")


def build_call_credit_spread(symbol: str, sim: MarketView, config: Config,
                             wing: float = 0.04) -> Optional[Order]:
    spot = sim.get_underlying(symbol)
    eday = _expiry_day(sim, config)
    sell_strike = _nearest_strike(spot, wing)
    buy_strike = _nearest_strike(spot, 2 * wing)
    legs = _make_legs(sim, symbol, "call", sell_strike, buy_strike, eday, config)
    if len(legs) < 2 or any(l.entry_price <= 0 for l in legs):
        return None
    net = legs[0].entry_price - legs[1].entry_price
    if net < config.min_credit_quality_spread:
        return None
    return Order(order_class="mleg", strategy="call_credit_spread", root=symbol,
                 legs=legs, qty=1,
                 note=f"sell {legs[0].strike}C / buy {legs[1].strike}C net {net:.2f}")


def build_iron_condor(symbol: str, sim: MarketView, config: Config,
                      wing: float = 0.05) -> Optional[Order]:
    spot = sim.get_underlying(symbol)
    eday = _expiry_day(sim, config)
    sell_p = _nearest_strike(spot, -wing)
    buy_p = _nearest_strike(spot, -2 * wing)
    sell_c = _nearest_strike(spot, wing)
    buy_c = _nearest_strike(spot, 2 * wing)
    p_legs = _make_legs(sim, symbol, "put", sell_p, buy_p, eday, config)
    c_legs = _make_legs(sim, symbol, "call", sell_c, buy_c, eday, config)
    legs = p_legs + c_legs
    if len(legs) < 4 or any(l.entry_price <= 0 for l in legs):
        return None
    net = (p_legs[0].entry_price - p_legs[1].entry_price) + \
          (c_legs[0].entry_price - c_legs[1].entry_price)
    if net < config.min_credit_quality_spread * 1.5:
        return None
    return Order(order_class="mleg", strategy="iron_condor", root=symbol,
                 legs=legs, qty=1,
                 note=f"IC {p_legs[0].strike}P/{p_legs[1].strike}P/"
                      f"{c_legs[0].strike}C/{c_legs[1].strike}C net {net:.2f}")


def wing_for_ivrank(iv_rank: float, base: float = 0.05, min_wing: float = 0.03,
                    max_wing: float = 0.07) -> float:
    """Adaptive wing width: tighten spreads when IV rank is extreme."""
    extremity = abs(iv_rank - 50) / 50.0
    wing = base - (base - min_wing) * extremity
    return round(min(max_wing, max(min_wing, wing)), 4)


def generate_candidates(symbol: str, sim: MarketView, config: Config,
                        signals: dict[str, Signal] | None = None) -> list[Order]:
    sig = signals.get(symbol) if signals else None
    if sig is None:
        sig = compute_signal(symbol, sim, config)
    wing = wing_for_ivrank(sig.iv_rank)
    candidates: list[Order] = []
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
    candidates.sort(key=lambda o: o.net_credit, reverse=True)
    return candidates
