from __future__ import annotations

from typing import Any

from iddiaarb.providers.betexplorer_scraper import BetExplorerScraperProvider


def _list_html() -> str:
    return """
    <table>
      <tr class="js-tournament">
        <td><a class="table-main__tournament">League One</a></td>
      </tr>
      <tr data-dt="16,2,2026,13,45">
        <td><a href="/football/x/y/AbC12345/">Home One - Away One</a></td>
      </tr>
      <tr data-dt="16,2,2026,15,00">
        <td><a href="/football/x/y/Def67890/">Home Two - Away Two</a></td>
      </tr>
    </table>
    """


def _odds_html() -> str:
    return """
    <table>
      <tr data-bid="417">
        <td><a class="in-bookmaker-logo-link">Book A</a></td>
        <td data-odd="2.05" data-pos="1"></td>
        <td data-odd="3.60" data-pos="0"></td>
        <td data-odd="4.20" data-pos="2"></td>
      </tr>
      <tr data-bid="429">
        <td><a class="in-bookmaker-logo-link">Book B</a></td>
        <td data-pos="1" data-odd="2.10"></td>
        <td data-pos="0" data-odd="3.50"></td>
        <td data-pos="2" data-odd="4.00"></td>
      </tr>
    </table>
    """


class FakeBetExplorerProvider(BetExplorerScraperProvider):
    def __init__(self, odds_by_match: dict[str, dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.odds_by_match = odds_by_match

    def _http_get_text(self, url: str, headers: dict[str, str]) -> str:  # type: ignore[override]
        if url == self.LIST_URL:
            return _list_html()
        raise AssertionError(f"Unexpected URL in test: {url}")

    def _http_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:  # type: ignore[override]
        marker = "/match-odds/"
        idx = url.find(marker)
        if idx < 0:
            raise AssertionError(f"Unexpected odds URL in test: {url}")
        match_id = url[idx + len(marker) :].split("/", 1)[0]
        if match_id not in self.odds_by_match:
            raise AssertionError(f"Missing test odds for match_id={match_id}")
        return self.odds_by_match[match_id]


def test_betexplorer_scraper_builds_events():
    provider = FakeBetExplorerProvider(
        odds_by_match={
            "AbC12345": {"odds": _odds_html()},
            "Def67890": {"odds": _odds_html()},
        },
        max_events=5,
        request_pause_sec=0.0,
    )

    events = provider.load_events()
    assert len(events) == 2
    assert events[0].event_id.startswith("betexplorer:")
    assert len(events[0].markets) == 2
    assert provider.last_diagnostics["events_with_at_least_two_books"] == 2
    assert provider.last_diagnostics["events_with_h2h"] == 2


def test_betexplorer_scraper_handles_empty_listing():
    class EmptyListProvider(FakeBetExplorerProvider):
        def _http_get_text(self, url: str, headers: dict[str, str]) -> str:  # type: ignore[override]
            return "<html><body>No rows</body></html>"

    provider = EmptyListProvider(odds_by_match={}, max_events=3)
    events = provider.load_events()
    assert events == []
    assert provider.last_diagnostics["events_listed"] == 0
