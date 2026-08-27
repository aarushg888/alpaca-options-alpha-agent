"""Configuration & credentials for the Alpaca Options Alpha Agent.

The agent auto-selects its execution backend:
  - If ALPACA_API_KEY and ALPACA_SECRET_KEY are present  -> "alpaca" (live paper)
  - Otherwise                                          -> "simulated" (local sim)

The simulated backend uses a deterministic local market simulator so the agent
is fully runnable and testable before any Alpaca credentials are added.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Load .env if present (no hard dependency on python-dotenv).
def _load_dotenv(path: Optional[Path] = None) -> None:
    p = path or Path(__file__).resolve().parents[2] / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Config:
    # --- Broker / Alpaca ---
    alpaca_api_key: Optional[str] = None
    alpaca_secret_key: Optional[str] = None
    alpaca_endpoint: str = "https://paper-api.alpaca.markets"
    alpaca_paper: bool = True
    alpaca_cli_bin: str = "alpaca"  # resolved via PATH / ~/go/bin

    # --- Account / risk budget ---
    starting_balance: float = 100_000.0
    max_gross_theta_per_day: float = 250.0       # sell-side income target cap
    max_portfolio_risk_pct: float = 0.04         # max loss / equity per position
    max_total_risk_pct: float = 0.20             # max aggregate defined risk
    max_positions: int = 8
    target_positions: int = 5
    max_single_position_pct: float = 0.06        # capital per position
    max_daily_drawdown_pct: float = 0.05
    max_total_drawdown_pct: float = 0.15
    min_dte: int = 21                            # don't open expiring-soon
    max_dte: int = 60                            # keep spreads short-dated
    iv_rank_min: float = 35.0                    # enter when IV rank in range
    iv_rank_max: float = 75.0
    min_credit_quality_spread: float = 0.20      # min $/share credit to bother

    # --- Simulation ---
    sim_seed: int = 1234
    sim_steps: int = 504  # ~1 trading year of daily steps
    sim_days_per_step: int = 1
    sim_mu: float = 0.05       # annual drift
    sim_sigma: float = 0.18    # annual vol of underlying
    sim_risk_free: float = 0.04
    sim_iv_base: float = 0.20
    sim_iv_term: float = 0.06  # extra vol for longer-dated

    # --- Agent cadence ---
    decision_interval_minutes: int = 15  # for live loop
    log_level: str = "INFO"
    results_dir: Path = field(default_factory=lambda: Path("runs"))

    # --- Tradable universe (liquid ETFs / names with active option chains) ---
    universe: list = field(default_factory=lambda: list(DEFAULT_UNIVERSE))

    @property
    def backend(self) -> str:
        if self.alpaca_api_key and self.alpaca_secret_key:
            return "alpaca"
        return "simulated"

    @classmethod
    def from_env(cls) -> "Config":
        def f(name, default, cast=float):
            v = os.environ.get(name)
            if v is None:
                return default
            try:
                return cast(v)
            except ValueError:
                return default

        return cls(
            alpaca_api_key=os.environ.get("ALPACA_API_KEY"),
            alpaca_secret_key=os.environ.get("ALPACA_SECRET_KEY"),
            alpaca_endpoint=os.environ.get("ALPACA_ENDPOINT", cls.alpaca_endpoint),
            alpaca_paper=os.environ.get("ALPACA_PAPER_TRADE", "true").lower()
            != "false",
            alpaca_cli_bin=os.environ.get("ALPACA_CLI_BIN", "alpaca"),
        )


# Default tradable universe: liquid ETFs with actively traded option chains.
DEFAULT_UNIVERSE = [
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "lvl": 560.0},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "lvl": 480.0},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "lvl": 220.0},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "lvl": 215.0},
    {"symbol": "AAPL", "name": "Apple Inc.", "lvl": 230.0},
    {"symbol": "NVDA", "name": "NVIDIA Corp.", "lvl": 135.0},
    {"symbol": "TSLA", "name": "Tesla Inc.", "lvl": 250.0},
    {"symbol": "AMD", "name": "Advanced Micro Devices", "lvl": 160.0},
    {"symbol": "XLE", "name": "Energy Select Sector SPDR", "lvl": 95.0},
    {"symbol": "XLF", "name": "Financial Select Sector SPDR", "lvl": 45.0},
]
