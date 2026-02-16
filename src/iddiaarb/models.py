from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass(frozen=True)
class OutcomeQuote:
    key: str
    label: str
    odds: float
    last_updated_epoch: int | None = None


@dataclass(frozen=True)
class MarketQuote:
    bookmaker: str
    market_key: str
    outcomes: List[OutcomeQuote]
    source: str = "unknown"
    quality_score: float = 0.5


@dataclass(frozen=True)
class Event:
    event_id: str
    sport: str
    league: str
    home: str
    away: str
    start_time: datetime
    markets: List[MarketQuote]
    sources: tuple[str, ...] = field(default_factory=tuple)
    data_quality_score: float = 0.5


@dataclass(frozen=True)
class StakePlan:
    requested_bankroll: float
    bankroll: float
    total_implied_probability: float
    allocations: Dict[str, float]
    guaranteed_return: float
    guaranteed_profit: float
    guaranteed_profit_pct: float
    constraints_applied: bool = False
    constraints_note: str = ""


@dataclass(frozen=True)
class ArbitrageOpportunity:
    event_id: str
    event_label: str
    market_key: str
    outcomes: Dict[str, float]
    bookmakers: Dict[str, str]
    total_implied_probability: float
    margin_pct: float
    stake_plan: StakePlan
    expected_profit: float = 0.0
    risk_penalty: float = 0.0
    risk_score: float = 0.0
    source_count: int = 1
    data_quality_score: float = 0.5
