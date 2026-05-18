from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

from iddiaarb.models import Event, MarketQuote, OutcomeQuote


def _find_curl_binary() -> str:
    if os.name == "nt":
        found = shutil.which("curl.exe") or shutil.which("curl")
    else:
        found = shutil.which("curl")
    return found or "curl"


class BetExplorerScrapeError(RuntimeError):
    pass


class BetExplorerScraperProvider:
    LIST_URL = "https://www.betexplorer.com/football/"
    ODDS_URL_TEMPLATE = "https://www.betexplorer.com/match-odds/{match_id}/0/1x2/odds/?lang={lang}"

    def __init__(
        self,
        max_events: int = 25,
        timeout_sec: int = 20,
        request_pause_sec: float = 0.05,
        prefer_curl: bool = True,
        retries: int = 2,
        lang: str = "en",
    ) -> None:
        self.max_events = max(1, max_events)
        self.timeout_sec = max(5, timeout_sec)
        self.request_pause_sec = max(0.0, request_pause_sec)
        self.prefer_curl = prefer_curl
        self.retries = max(0, retries)
        self.lang = (lang or "en").strip().lower()
        self.last_diagnostics: dict[str, Any] = {}

    def _http_get_text_via_curl(self, url: str, headers: dict[str, str]) -> str:
        curl_bin = _find_curl_binary()
        cmd = [curl_bin, "-s", "-S", "-L", "--max-time", str(self.timeout_sec)]
        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])
        cmd.append(url)

        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise BetExplorerScrapeError("curl not found in PATH") from exc

        if proc.returncode != 0:
            raise BetExplorerScrapeError(f"curl failed ({proc.returncode}) for {url}: {proc.stderr.strip()}")
        out = proc.stdout
        if not out:
            raise BetExplorerScrapeError(f"Empty response from {url}")
        return out

    def _http_get_text_urllib(self, url: str, headers: dict[str, str]) -> str:
        req = Request(url=url, method="GET", headers=headers)
        with urlopen(req, timeout=self.timeout_sec) as response:
            return response.read().decode("utf-8", errors="replace")

    def _http_get_text(self, url: str, headers: dict[str, str]) -> str:
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                if self.prefer_curl:
                    return self._http_get_text_via_curl(url, headers)
                return self._http_get_text_urllib(url, headers)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                try:
                    if self.prefer_curl:
                        return self._http_get_text_urllib(url, headers)
                    return self._http_get_text_via_curl(url, headers)
                except Exception as exc2:  # noqa: BLE001
                    last_err = exc2
                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                break
        raise BetExplorerScrapeError(f"HTTP fetch failed for {url}: {last_err}") from last_err

    def _http_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        raw = self._http_get_text(url, headers=headers)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BetExplorerScrapeError(f"Invalid JSON from {url}") from exc
        if not isinstance(payload, dict):
            raise BetExplorerScrapeError(f"Unexpected payload type from {url}: {type(payload).__name__}")
        return payload

    @staticmethod
    def _strip_tags(raw_html: str) -> str:
        return html.unescape(re.sub(r"<[^>]+>", "", raw_html or "")).strip()

    @staticmethod
    def _parse_dt(dt_raw: str) -> datetime | None:
        parts = [p.strip() for p in (dt_raw or "").split(",")]
        if len(parts) != 5:
            return None
        try:
            day, month, year, hour, minute = [int(p) for p in parts]
            return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None

    def _parse_events_table(self, page_html: str, max_rows: int | None = None) -> list[dict[str, Any]]:
        rows = re.finditer(r"(<tr[^>]*>)(.*?)</tr>", page_html, flags=re.IGNORECASE | re.DOTALL)
        out: list[dict[str, Any]] = []
        current_league = "BetExplorer Football"

        for row in rows:
            row_open = row.group(1)
            row_body = row.group(2)

            if "js-tournament" in row_open:
                league_match = re.search(
                    r'<a[^>]*class="table-main__tournament"[^>]*>(.*?)</a>',
                    row_body,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if league_match:
                    current_league = self._strip_tags(league_match.group(1))
                continue

            dt_match = re.search(r'data-dt="([^"]+)"', row_open)
            if not dt_match:
                continue
            start_time = self._parse_dt(dt_match.group(1))
            if not start_time:
                continue

            href_match = re.search(
                r'<a href="(/football/[^"]+/([A-Za-z0-9]+)/)"[^>]*>(.*?)</a>',
                row_body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not href_match:
                continue
            match_path = href_match.group(1)
            match_id = href_match.group(2)
            teams_text = self._strip_tags(href_match.group(3))
            if " - " not in teams_text:
                continue
            home, away = [p.strip() for p in teams_text.split(" - ", 1)]
            if not home or not away:
                continue

            out.append(
                {
                    "match_id": match_id,
                    "match_path": match_path,
                    "league": current_league,
                    "home": home,
                    "away": away,
                    "start_time": start_time,
                }
            )

            if max_rows is not None and len(out) >= max_rows:
                break

        return out

    def _fetch_match_odds_html(self, match_id: str, match_path: str) -> str:
        url = self.ODDS_URL_TEMPLATE.format(match_id=match_id, lang=self.lang)
        payload = self._http_get_json(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0",
                "Referer": f"https://www.betexplorer.com{match_path}",
                "Origin": "https://www.betexplorer.com",
            },
        )
        odds_html = payload.get("odds")
        if not isinstance(odds_html, str) or not odds_html.strip():
            raise BetExplorerScrapeError(f"Missing odds html for match {match_id}")
        return odds_html

    def _parse_market_quotes(self, odds_html: str) -> list[MarketQuote]:
        row_chunks: list[str] = []
        row_chunks.extend(
            match.group(1)
            for match in re.finditer(
                r'(<tr[^>]*data-bid="[0-9]+"[^>]*>.*?</tr>)',
                odds_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        row_chunks.extend(
            match.group(1)
            for match in re.finditer(
                r'(<div[^>]*oddsComparisonAll__rowBookie[^>]*data-bid="[0-9]+"[^>]*>.*?)(?=<div[^>]*oddsComparisonAll__rowBookie|<div id="match-add-to-selection"|$)',
                odds_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )

        markets: list[MarketQuote] = []

        for row_html in row_chunks:
            book_match = re.search(
                r'in-bookmaker-logo-link[^>]*>([^<]+)</a>',
                row_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not book_match:
                continue
            bookmaker = self._strip_tags(book_match.group(1))
            if not bookmaker:
                continue

            outcomes: dict[str, float] = {}
            pair_rx = re.compile(
                r'data-odd="([0-9]+(?:\.[0-9]+)?)"[^>]*data-pos="([012])"|data-pos="([012])"[^>]*data-odd="([0-9]+(?:\.[0-9]+)?)"',
                flags=re.IGNORECASE,
            )
            for pair in pair_rx.finditer(row_html):
                if pair.group(1) and pair.group(2):
                    odd = float(pair.group(1))
                    pos = pair.group(2)
                else:
                    odd = float(pair.group(4))
                    pos = pair.group(3)
                if odd <= 1:
                    continue
                outcomes[pos] = max(outcomes.get(pos, 0.0), odd)

            if not {"0", "1", "2"}.issubset(outcomes.keys()):
                continue

            markets.append(
                MarketQuote(
                    bookmaker=bookmaker,
                    market_key="h2h_prematch",
                    outcomes=[
                        OutcomeQuote(key="HOME", label="1", odds=outcomes["1"]),
                        OutcomeQuote(key="DRAW", label="X", odds=outcomes["0"]),
                        OutcomeQuote(key="AWAY", label="2", odds=outcomes["2"]),
                    ],
                    source="betexplorer",
                    quality_score=0.78,
                )
            )
        return markets

    def load_events(self) -> list[Event]:
        now = datetime.now(timezone.utc)
        list_html = self._http_get_text(
            self.LIST_URL,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.google.com/",
            },
        )
        lower = list_html.lower()
        looks_html = any(tag in lower for tag in ("<html", "<body", "<!doctype", "<table", "<div"))
        if not looks_html:
            snippet = list_html[:120].strip().replace("\n", " ")
            raise BetExplorerScrapeError(
                f"Unexpected listing response from {self.LIST_URL} (not HTML): {snippet!r}"
            )
        scan_limit = min(max(20, self.max_events * 6), 300)
        listed_events = self._parse_events_table(list_html, max_rows=scan_limit)
        if not listed_events:
            self.last_diagnostics = {
                "source": "betexplorer_scraper",
                "fetched_at": now.isoformat(),
                "events_listed": 0,
                "events_with_h2h": 0,
                "odds_failed": 0,
                "events_with_at_least_two_books": 0,
                "max_distinct_books_per_event": 0,
                "min_distinct_books_per_event": 0,
                "arbitrage_blocker": "BetExplorer listing parsed 0 events.",
            }
            return []

        events: list[Event] = []
        odds_failed = 0
        books_per_event: list[int] = []

        for row in listed_events:
            match_id = str(row["match_id"])
            match_path = str(row["match_path"])
            try:
                odds_html = self._fetch_match_odds_html(match_id, match_path)
                markets = self._parse_market_quotes(odds_html)
            except BetExplorerScrapeError:
                odds_failed += 1
                continue

            if not markets:
                continue

            events.append(
                Event(
                    event_id=f"betexplorer:{match_id}",
                    sport="football",
                    league=str(row["league"]),
                    home=str(row["home"]),
                    away=str(row["away"]),
                    start_time=row["start_time"],
                    markets=markets,
                    sources=("betexplorer",),
                    data_quality_score=0.79,
                )
            )
            books_per_event.append(len({m.bookmaker for m in markets}))

            if self.request_pause_sec > 0:
                time.sleep(self.request_pause_sec)
            if len(events) >= self.max_events:
                break

        events_with_two_books = sum(1 for c in books_per_event if c >= 2)
        self.last_diagnostics = {
            "source": "betexplorer_scraper",
            "fetched_at": now.isoformat(),
            "events_listed": len(listed_events),
            "events_scan_limit": scan_limit,
            "events_with_h2h": len(events),
            "odds_failed": odds_failed,
            "events_with_at_least_two_books": events_with_two_books,
            "max_distinct_books_per_event": max(books_per_event) if books_per_event else 0,
            "min_distinct_books_per_event": min(books_per_event) if books_per_event else 0,
            "lang": self.lang,
            "list_url": self.LIST_URL,
        }
        if events and events_with_two_books == 0:
            self.last_diagnostics["arbitrage_blocker"] = (
                "No event has >=2 distinct bookmakers in scraped BetExplorer odds tables."
            )
        return events


__all__ = ["BetExplorerScraperProvider", "BetExplorerScrapeError"]
