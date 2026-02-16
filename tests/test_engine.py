from datetime import datetime, timezone

from iddiaarb.engine import ArbitrageEngine
from iddiaarb.models import Event, MarketQuote, OutcomeQuote


def _event_two_way(home_odd: float, away_odd: float) -> Event:
    return Event(
        event_id="e1",
        sport="tennis",
        league="test",
        home="A",
        away="B",
        start_time=datetime.now(tz=timezone.utc),
        markets=[
            MarketQuote(
                bookmaker="Book1",
                market_key="moneyline",
                outcomes=[
                    OutcomeQuote(key="HOME", label="A", odds=home_odd),
                    OutcomeQuote(key="AWAY", label="B", odds=1.8),
                ],
            ),
            MarketQuote(
                bookmaker="Book2",
                market_key="moneyline",
                outcomes=[
                    OutcomeQuote(key="HOME", label="A", odds=1.9),
                    OutcomeQuote(key="AWAY", label="B", odds=away_odd),
                ],
            ),
        ],
    )


def test_two_way_arbitrage_exists():
    event = _event_two_way(home_odd=2.1, away_odd=2.1)
    engine = ArbitrageEngine(min_margin_pct=0.0)
    result = engine.scan([event], bankroll=1000)
    assert len(result) == 1
    assert result[0].margin_pct > 0
    assert abs(sum(result[0].stake_plan.allocations.values()) - 1000) < 1e-6


def test_two_way_arbitrage_not_exists():
    event = _event_two_way(home_odd=1.9, away_odd=1.9)
    engine = ArbitrageEngine(min_margin_pct=0.0)
    result = engine.scan([event], bankroll=1000)
    assert len(result) == 0


def test_three_way_arbitrage_exists():
    event = Event(
        event_id="e3",
        sport="football",
        league="test",
        home="A",
        away="B",
        start_time=datetime.now(tz=timezone.utc),
        markets=[
            MarketQuote(
                bookmaker="Book1",
                market_key="1x2",
                outcomes=[
                    OutcomeQuote(key="HOME", label="1", odds=2.55),
                    OutcomeQuote(key="DRAW", label="X", odds=3.00),
                    OutcomeQuote(key="AWAY", label="2", odds=3.10),
                ],
            ),
            MarketQuote(
                bookmaker="Book2",
                market_key="1x2",
                outcomes=[
                    OutcomeQuote(key="HOME", label="1", odds=2.20),
                    OutcomeQuote(key="DRAW", label="X", odds=3.40),
                    OutcomeQuote(key="AWAY", label="2", odds=3.30),
                ],
            ),
        ],
    )
    engine = ArbitrageEngine(min_margin_pct=0.1)
    result = engine.scan([event], bankroll=1000)
    assert len(result) == 1
    assert result[0].margin_pct > 0.1

