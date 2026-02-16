from __future__ import annotations

from pathlib import Path

from iddiaarb.notifier import _filter_meaningful_alerts


def _opp(margin: float) -> dict:
    return {
        "event_id": "e1",
        "market": "h2h",
        "bookmakers": {"HOME": "A", "AWAY": "B"},
        "margin_pct": margin,
    }


def test_notifier_filters_small_changes(tmp_path: Path):
    state = tmp_path / "alerts.json"
    first = _filter_meaningful_alerts([_opp(1.0)], str(state), min_margin_delta=0.15)
    second = _filter_meaningful_alerts([_opp(1.05)], str(state), min_margin_delta=0.15)
    third = _filter_meaningful_alerts([_opp(1.25)], str(state), min_margin_delta=0.15)
    assert len(first) == 1
    assert len(second) == 0
    assert len(third) == 1
