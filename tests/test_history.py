from __future__ import annotations

from pathlib import Path

from iddiaarb.history import backtest_report, save_run


def _sample(event_id: str, margin: float) -> dict:
    return {
        "event_id": event_id,
        "event": f"{event_id} vs X",
        "market": "h2h",
        "bookmakers": {"HOME": "A", "AWAY": "B"},
        "margin_pct": margin,
        "profit": 5.0,
        "expected_profit": 4.5,
        "risk_score": 10.0,
    }


def test_history_report_tracks_reappearance(tmp_path: Path):
    db = tmp_path / "h.db"
    save_run(str(db), "scan", {"a": 1}, [_sample("ev1", 1.2), _sample("ev2", 0.9)])
    save_run(str(db), "scan", {"a": 2}, [_sample("ev1", 1.3)])
    rep = backtest_report(str(db), last_runs=10)
    assert rep.runs == 2
    assert rep.total_records == 3
    assert rep.reappeared_next_run_ratio > 0
