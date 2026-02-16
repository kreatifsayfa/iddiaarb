from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from iddiaarb.providers.livesport_scraper import (
    LivesportScraperProvider,
    build_livesport_provider,
    build_soccer24_provider,
)


def _menu_payload() -> dict[str, Any]:
    return {
        "data": {
            "getPrematchOddsBettingTypeMenu": {
                "settings": {
                    "bookmakers": [
                        {"bookmaker": {"id": 16, "name": "bet365"}},
                        {"bookmaker": {"id": 26, "name": "Betway"}},
                    ]
                },
                "items": [
                    {
                        "isActive": True,
                        "bettingType": "HOME_DRAW_AWAY",
                        "bettingScope": "FULL_TIME",
                        "bookmakerIds": [16, 26],
                    }
                ],
            }
        }
    }


def _odds_payload(home: str, draw: str, away: str) -> dict[str, Any]:
    return {
        "data": {
            "findPrematchOddsForBookmaker": {
                "home": {"value": home},
                "draw": {"value": draw},
                "away": {"value": away},
            }
        }
    }


class FakeLivesportProvider(LivesportScraperProvider):
    def __init__(
        self,
        feed_text: str,
        menu_map: dict[str, dict[str, Any]],
        odds_map: dict[tuple[str, int], dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._feed_text = feed_text
        self._menu_map = menu_map
        self._odds_map = odds_map

    def _http_get_text(self, url: str, headers: dict[str, str]) -> str:  # type: ignore[override]
        if url == self.feed_url:
            return self._feed_text
        raise AssertionError(f"Unexpected URL in test: {url}")

    def _http_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:  # type: ignore[override]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        hash_name = (query.get("_hash") or [""])[0]
        event_id = (query.get("eventId") or [""])[0]
        if hash_name == "pobtm":
            return self._menu_map[event_id]
        if hash_name in {"ope2", "ope"}:
            bookmaker_id = int((query.get("bookmakerId") or ["0"])[0])
            return self._odds_map[(event_id, bookmaker_id)]
        raise AssertionError(f"Unexpected _hash in test URL: {hash_name}")


def test_livesport_scraper_builds_multi_bookmaker_events():
    sep = chr(172)
    feed_text = sep.join(
        [
            "SAÃ·1",
            "~ZAÃ·League One",
            "~AAÃ·EVT00001",
            "ADÃ·1772000000",
            "AEÃ·Home One",
            "AFÃ·Away One",
            "~AAÃ·EVT00002",
            "ADÃ·1772003600",
            "AEÃ·Home Two",
            "AFÃ·Away Two",
        ]
    )
    menu_map = {"EVT00001": _menu_payload(), "EVT00002": _menu_payload()}
    odds_map = {
        ("EVT00001", 16): _odds_payload("1.90", "3.30", "4.20"),
        ("EVT00001", 26): _odds_payload("1.95", "3.20", "4.00"),
        ("EVT00002", 16): _odds_payload("2.10", "3.40", "3.30"),
        ("EVT00002", 26): _odds_payload("2.05", "3.45", "3.50"),
    }
    provider = FakeLivesportProvider(
        source_name="soccer24",
        project_id="100",
        feed_url="https://100.flashscore.ninja/100/x/feed/f_1_0_3_en_1",
        referer="https://www.soccer24.com/",
        feed_text=feed_text,
        menu_map=menu_map,
        odds_map=odds_map,
        max_events=10,
        request_pause_sec=0.0,
    )

    events = provider.load_events()
    assert len(events) == 2
    assert len(events[0].markets) == 2
    assert events[0].event_id.startswith("soccer24:")
    assert provider.last_diagnostics["events_with_at_least_two_books"] == 2
    assert provider.last_diagnostics["project_id"] == "100"


def test_livesport_builders_have_expected_endpoints():
    soccer24 = build_soccer24_provider(max_events=3)
    livesport = build_livesport_provider(max_events=3)

    assert soccer24.project_id == "100"
    assert "100.flashscore.ninja" in soccer24.feed_url
    assert soccer24.source_name == "soccer24"

    assert livesport.project_id == "500"
    assert "500.flashscore.ninja" in livesport.feed_url
    assert livesport.source_name == "livesport"
