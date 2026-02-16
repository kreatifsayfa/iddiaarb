from __future__ import annotations

from pathlib import Path
from typing import Any

from iddiaarb.providers.flashscore_scraper import FlashscoreScraperProvider


class ReplayProvider(FlashscoreScraperProvider):
    def __init__(self, feed_text: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.feed_text = feed_text

    def _http_get_text(self, url: str, headers: dict[str, str]) -> str:  # type: ignore[override]
        if url == self.FEED_URL:
            return self.feed_text
        raise RuntimeError(f"Unexpected replay url: {url}")


def test_replay_feed_parsing_from_fixture():
    feed = Path("tests/fixtures/flashscore_feed_sample.txt").read_text(encoding="utf-8")
    provider = ReplayProvider(feed_text=feed)
    rows = provider._fetch_feed_events()  # noqa: SLF001 - intentional replay contract test
    assert len(rows) == 2
    assert rows[0]["event_id"] == "ABC12345"
    assert rows[0]["home"] == "Home Team"
    assert rows[1]["away"] == "Second Away"
