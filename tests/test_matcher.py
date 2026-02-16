from datetime import datetime, timezone

from iddiaarb.matcher import event_confidence, merge_similar_events
from iddiaarb.models import Event, MarketQuote, OutcomeQuote


def _mk_event(event_id: str, home: str, away: str, minute_offset: int = 0) -> Event:
    return Event(
        event_id=event_id,
        sport="football",
        league="Super Lig",
        home=home,
        away=away,
        start_time=datetime(2026, 2, 15, 18, minute_offset, tzinfo=timezone.utc),
        markets=[
            MarketQuote(
                bookmaker=event_id,
                market_key="1x2",
                outcomes=[
                    OutcomeQuote(key="HOME", label="1", odds=2.1),
                    OutcomeQuote(key="DRAW", label="X", odds=3.2),
                    OutcomeQuote(key="AWAY", label="2", odds=3.1),
                ],
            )
        ],
    )


def test_event_confidence_is_high_for_similar_names():
    left = _mk_event("a", "Team A", "Team B")
    right = _mk_event("b", "Team A FC", "Team B")
    score = event_confidence(left, right)
    assert score > 0.9


def test_merge_similar_events_clusters_bookmakers():
    events = [
        _mk_event("book1", "Team A", "Team B", 0),
        _mk_event("book2", "Team A FC", "Team B", 4),
        _mk_event("book3", "Team A", "Team B", 1),
    ]
    merged, decisions = merge_similar_events(events, min_confidence=0.85)
    assert len(merged) == 1
    assert len(decisions) == 2
    assert len(merged[0].markets) == 3


def test_event_confidence_handles_common_abbreviations():
    left = _mk_event("a", "Gimnasia L.P.", "Estudiantes L.P.")
    right = _mk_event("b", "Gimnasia y Esgrima", "Estudiantes de La Plata")
    score = event_confidence(left, right)
    assert score > 0.75
