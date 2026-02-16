from __future__ import annotations

from datetime import datetime, timezone

from iddiaarb.engine import ArbitrageEngine
from iddiaarb.limits import BookmakerLimit
from iddiaarb.models import Event, MarketQuote, OutcomeQuote


def _event() -> Event:
    return Event(
        event_id="e-risk",
        sport="football",
        league="test",
        home="A",
        away="B",
        start_time=datetime.now(tz=timezone.utc),
        markets=[
            MarketQuote(
                bookmaker="Book1",
                market_key="h2h",
                outcomes=[
                    OutcomeQuote(key="HOME", label="1", odds=2.1),
                    OutcomeQuote(key="DRAW", label="X", odds=3.6),
                    OutcomeQuote(key="AWAY", label="2", odds=4.6),
                ],
            ),
            MarketQuote(
                bookmaker="Book2",
                market_key="h2h",
                outcomes=[
                    OutcomeQuote(key="HOME", label="1", odds=2.0),
                    OutcomeQuote(key="DRAW", label="X", odds=3.8),
                    OutcomeQuote(key="AWAY", label="2", odds=3.6),
                ],
            ),
        ],
        sources=("flashscore", "sofascore"),
        data_quality_score=0.9,
    )


def test_limits_apply_and_reduce_used_bankroll():
    limits = {"Book2": BookmakerLimit(max_stake=50)}
    engine = ArbitrageEngine(min_margin_pct=0.0, bookmaker_limits=limits)
    result = engine.scan([_event()], bankroll=1000)
    assert len(result) == 1
    opp = result[0]
    assert opp.stake_plan.bankroll < 1000
    assert opp.stake_plan.constraints_applied


def test_risk_penalty_reduces_expected_profit():
    engine = ArbitrageEngine(min_margin_pct=0.0, slippage_bps=80, rejection_rate=0.25)
    result = engine.scan([_event()], bankroll=1000)
    assert len(result) == 1
    opp = result[0]
    assert opp.expected_profit < opp.stake_plan.guaranteed_profit
    assert opp.risk_penalty > 0
