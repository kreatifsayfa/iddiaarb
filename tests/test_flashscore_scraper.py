from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from iddiaarb.providers.flashscore_scraper import FlashscoreScraperProvider


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


class FakeFlashscoreProvider(FlashscoreScraperProvider):
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
        if url == self.FEED_URL:
            return self._feed_text
        raise AssertionError(f"Unexpected URL in test: {url}")

    def _http_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:  # type: ignore[override]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        hash_name = (query.get("_hash") or [""])[0]
        event_id = (query.get("eventId") or [""])[0]
        if hash_name == "pobtm":
            return self._menu_map[event_id]
        if hash_name == "ope2":
            bookmaker_id = int((query.get("bookmakerId") or ["0"])[0])
            return self._odds_map[(event_id, bookmaker_id)]
        raise AssertionError(f"Unexpected _hash in test URL: {hash_name}")


def test_flashscore_scraper_builds_multi_bookmaker_events():
    sep = chr(172)
    feed_text = sep.join(
        [
            "SA÷1",
            "~ZA÷League One",
            "~AA÷EVT00001",
            "AD÷1772000000",
            "AE÷Home One",
            "AF÷Away One",
            "~AA÷EVT00002",
            "AD÷1772003600",
            "AE÷Home Two",
            "AF÷Away Two",
        ]
    )
    menu_map = {"EVT00001": _menu_payload(), "EVT00002": _menu_payload()}
    odds_map = {
        ("EVT00001", 16): _odds_payload("1.90", "3.30", "4.20"),
        ("EVT00001", 26): _odds_payload("1.95", "3.20", "4.00"),
        ("EVT00002", 16): _odds_payload("2.10", "3.40", "3.30"),
        ("EVT00002", 26): _odds_payload("2.05", "3.45", "3.50"),
    }
    provider = FakeFlashscoreProvider(
        feed_text=feed_text,
        menu_map=menu_map,
        odds_map=odds_map,
        max_events=10,
        request_pause_sec=0.0,
    )

    events = provider.load_events()
    assert len(events) == 2
    assert len(events[0].markets) == 2
    assert provider.last_diagnostics["events_with_at_least_two_books"] == 2
    assert provider.last_diagnostics["max_distinct_books_per_event"] == 2


def test_flashscore_scraper_skips_incomplete_feed_row():
    sep = chr(172)
    feed_text = sep.join(
        [
            "SA÷1",
            "~ZA÷League One",
            "~AA÷EVT00003",
            "AD÷1772000000",
            "AE÷Only Home",
            # missing AF (away)
        ]
    )
    provider = FakeFlashscoreProvider(
        feed_text=feed_text,
        menu_map={},
        odds_map={},
        max_events=10,
        request_pause_sec=0.0,
    )

    events = provider.load_events()
    assert events == []
    assert provider.last_diagnostics["events_with_h2h"] == 0
