from __future__ import annotations

import time
from typing import Any

from iddiaarb.providers.sofascore_scraper import SofaScoreScraperProvider


def _live_events_payload(change_ts: int) -> dict[str, Any]:
    return {
        "events": [
            {
                "id": 101,
                "startTimestamp": change_ts + 3600,
                "changes": {"changeTimestamp": change_ts},
                "tournament": {"name": "Liga", "uniqueTournament": {"name": "Liga"}},
                "homeTeam": {"name": "Home"},
                "awayTeam": {"name": "Away"},
            }
        ]
    }


def _odds_payload(home_frac: str, draw_frac: str, away_frac: str) -> dict[str, Any]:
    return {
        "eventId": 101,
        "markets": [
            {
                "marketName": "Full time",
                "sourceId": 1001,
                "isLive": True,
                "choices": [
                    {"name": "1", "fractionalValue": home_frac},
                    {"name": "X", "fractionalValue": draw_frac},
                    {"name": "2", "fractionalValue": away_frac},
                ],
            },
            {
                "marketName": "Full time",
                "sourceId": 1002,
                "isLive": False,
                "choices": [
                    {"name": "1", "fractionalValue": "1/1"},
                    {"name": "X", "fractionalValue": "2/1"},
                    {"name": "2", "fractionalValue": "3/1"},
                ],
            },
        ],
    }


class FakeSofaProvider(SofaScoreScraperProvider):
    def __init__(self, queue: dict[str, list[dict[str, Any]]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.queue = {k: list(v) for k, v in queue.items()}

    def _http_get_json(self, path: str) -> dict[str, Any]:  # type: ignore[override]
        values = self.queue.get(path)
        if not values:
            raise RuntimeError(f"Unexpected path in test: {path}")
        if len(values) == 1:
            return values[0]
        return values.pop(0)


def test_sofascore_scraper_builds_events_and_reports_freshness_change():
    now = int(time.time())
    provider = FakeSofaProvider(
        {
            "/sport/football/events/live": [_live_events_payload(change_ts=now - 30)],
            "/event/101/odds/1/all": [
                _odds_payload("1/1", "2/1", "3/1"),
                _odds_payload("2/1", "2/1", "3/1"),  # changed on sample check
            ],
        },
        stale_threshold_sec=120,
        sample_check=1,
    )

    events = provider.load_events()
    assert len(events) == 1
    assert len(events[0].markets) == 1
    assert provider.last_diagnostics["freshness_status"] == "fresh"
    assert provider.last_diagnostics["sample_changed"] == 1
    assert provider.last_diagnostics["stale_events_count"] == 0


def test_sofascore_scraper_marks_stale_risk():
    now = int(time.time())
    provider = FakeSofaProvider(
        {
            "/sport/football/events/live": [_live_events_payload(change_ts=now - 5000)],
            "/event/101/odds/1/all": [_odds_payload("1/1", "2/1", "3/1")],
        },
        stale_threshold_sec=60,
        sample_check=0,
    )

    provider.load_events()
    assert provider.last_diagnostics["stale_events_count"] == 1
    assert provider.last_diagnostics["freshness_status"] == "stale_risk"
