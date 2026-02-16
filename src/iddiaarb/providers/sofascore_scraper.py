from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from iddiaarb.models import Event, MarketQuote, OutcomeQuote


def _find_curl_binary() -> str:
    """Find the curl binary on the system, cross-platform."""
    if os.name == "nt":
        found = shutil.which("curl.exe") or shutil.which("curl")
    else:
        found = shutil.which("curl")
    return found or "curl"


class SofaScrapeError(RuntimeError):
    pass


class SofaScoreScraperProvider:
    BASE_URL = "https://www.sofascore.com/api/v1"
    BASE_URLS = (
        "https://www.sofascore.com/api/v1",
        "https://api.sofascore.com/api/v1",
    )

    def __init__(
        self,
        max_events: int = 40,
        stale_threshold_sec: int = 900,
        sample_check: int = 5,
        timeout_sec: int = 20,
        prefer_curl: bool = True,
        include_prematch: bool = False,
    ) -> None:
        self.max_events = max(1, max_events)
        self.stale_threshold_sec = max(1, stale_threshold_sec)
        self.sample_check = max(0, sample_check)
        self.timeout_sec = max(5, timeout_sec)
        self.prefer_curl = prefer_curl
        self.include_prematch = include_prematch
        self.last_diagnostics: dict[str, Any] = {}
        self.last_base_url_used = self.BASE_URL

    def _http_get_json(self, path: str) -> dict[str, Any]:
        last_err: Exception | None = None
        for base_url in self.BASE_URLS:
            url = f"{base_url}{path}"
            try:
                if self.prefer_curl:
                    payload = self._http_get_json_via_curl(url)
                else:
                    payload = self._http_get_json_via_urllib(url)
                self.last_base_url_used = base_url
                return payload
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        raise SofaScrapeError(f"All SofaScore base URLs failed for {path}: {last_err}") from last_err

    def _http_get_json_via_urllib(self, url: str) -> dict[str, Any]:
        req = Request(
            url=url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Origin": "https://www.sofascore.com",
                "Referer": "https://www.sofascore.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            },
            method="GET",
        )
        try:
            with urlopen(req, timeout=self.timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            # SofaScore frequently blocks urllib signatures with 403.
            if exc.code == 403:
                return self._http_get_json_via_curl(url)
            body = exc.read().decode("utf-8", errors="replace")
            raise SofaScrapeError(f"HTTP {exc.code} on {url}: {body}") from exc
        except URLError as exc:
            raise SofaScrapeError(f"Network error on {url}: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SofaScrapeError(f"Invalid JSON from {url}") from exc
        if not isinstance(payload, dict):
            raise SofaScrapeError(f"Unexpected payload for {url}: {type(payload).__name__}")
        return payload

    def _http_get_json_via_curl(self, url: str) -> dict[str, Any]:
        curl_bin = _find_curl_binary()
        try:
            proc = subprocess.run(
                [
                    curl_bin,
                    "-s",
                    "-S",
                    "-L",
                    "--max-time",
                    str(self.timeout_sec),
                    "-H",
                    "Accept: application/json, text/plain, */*",
                    "-H",
                    "Accept-Language: en-US,en;q=0.9",
                    "-H",
                    "Cache-Control: no-cache",
                    "-H",
                    "Pragma: no-cache",
                    "-H",
                    "Origin: https://www.sofascore.com",
                    "-H",
                    "Referer: https://www.sofascore.com/",
                    "-H",
                    (
                        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise SofaScrapeError("curl not found in PATH. Install curl or use urllib mode.") from exc

        if proc.returncode != 0:
            raise SofaScrapeError(
                f"curl failed ({proc.returncode}) for {url}: {proc.stderr.strip()}"
            )
        raw = proc.stdout.strip()
        if not raw:
            raise SofaScrapeError(f"Empty response from {url}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SofaScrapeError(f"Invalid JSON from {url}") from exc
        if not isinstance(payload, dict):
            raise SofaScrapeError(f"Unexpected payload type from {url}: {type(payload).__name__}")
        return payload

    @staticmethod
    def _fractional_to_decimal(value: str) -> float | None:
        raw = (value or "").strip().upper()
        if not raw:
            return None
        if raw in {"EVS", "EVEN", "EVENS"}:
            return 2.0
        if "/" in raw:
            left, right = raw.split("/", 1)
            try:
                num = float(left)
                den = float(right)
            except ValueError:
                return None
            if den <= 0:
                return None
            decimal = 1 + (num / den)
            return decimal if decimal > 1 else None
        try:
            decimal = float(raw)
        except ValueError:
            return None
        return decimal if decimal > 1 else None

    def _build_market_quotes(
        self,
        odds_payload: dict[str, Any],
    ) -> tuple[list[MarketQuote], tuple[str, ...]]:
        markets = odds_payload.get("markets")
        if not isinstance(markets, list):
            return [], tuple()

        out: list[MarketQuote] = []
        signatures: list[str] = []
        for market in markets:
            if not isinstance(market, dict):
                continue
            if market.get("marketName") != "Full time":
                continue

            choices = market.get("choices")
            if not isinstance(choices, list):
                continue

            values: dict[str, float] = {}
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                name = str(choice.get("name", "")).strip().upper()
                if name not in {"1", "X", "2"}:
                    continue
                fractional = str(choice.get("fractionalValue", "") or "")
                decimal = self._fractional_to_decimal(fractional)
                if decimal is None:
                    continue
                values[name] = decimal

            if set(values.keys()) != {"1", "X", "2"}:
                continue

            source_id = str(market.get("sourceId") or market.get("fid") or market.get("id") or "0")
            is_live = bool(market.get("isLive"))
            if (not is_live) and (not self.include_prematch):
                continue
            bookmaker = f"SofaScore-{'live' if is_live else 'prematch'}-{source_id}"
            outcomes = [
                OutcomeQuote(key="HOME", label="1", odds=values["1"]),
                OutcomeQuote(key="DRAW", label="X", odds=values["X"]),
                OutcomeQuote(key="AWAY", label="2", odds=values["2"]),
            ]
            out.append(
                MarketQuote(
                    bookmaker=bookmaker,
                    market_key="h2h_live" if is_live else "h2h_prematch",
                    outcomes=outcomes,
                    source="sofascore",
                    quality_score=0.6 if is_live else 0.55,
                )
            )
            signatures.append(
                "|".join(
                    [
                        source_id,
                        "L" if is_live else "P",
                        f"{values['1']:.6f}",
                        f"{values['X']:.6f}",
                        f"{values['2']:.6f}",
                    ]
                )
            )

        return out, tuple(sorted(signatures))

    def _event_change_age(self, event_row: dict[str, Any], now_ts: float) -> float | None:
        changes = event_row.get("changes")
        if not isinstance(changes, dict):
            return None
        ts = changes.get("changeTimestamp")
        if not isinstance(ts, (int, float)) or ts <= 0:
            return None
        age = now_ts - float(ts)
        return age if age >= 0 else 0.0

    def load_events(self) -> list[Event]:
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        payload = self._http_get_json("/sport/football/events/live")
        rows = payload.get("events")
        if not isinstance(rows, list):
            raise SofaScrapeError("Invalid events payload from SofaScore live endpoint")

        events: list[Event] = []
        stale_ids: list[str] = []
        change_ages: list[float] = []
        signatures_initial: dict[str, tuple[str, ...]] = {}

        for row in rows[: self.max_events]:
            if not isinstance(row, dict):
                continue
            event_id = row.get("id")
            if not isinstance(event_id, (int, str)):
                continue
            event_id_str = str(event_id)

            try:
                odds = self._http_get_json(f"/event/{event_id_str}/odds/1/all")
            except SofaScrapeError:
                continue
            if isinstance(odds.get("error"), dict):
                continue

            market_quotes, signature = self._build_market_quotes(odds)
            if not market_quotes:
                continue

            start_ts = row.get("startTimestamp")
            if not isinstance(start_ts, (int, float)):
                continue
            start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc)

            tournament = row.get("tournament", {})
            unique_tournament = (
                tournament.get("uniqueTournament", {}) if isinstance(tournament, dict) else {}
            )
            league = str(
                unique_tournament.get("name")
                or tournament.get("name")
                or "SofaScore Football"
            )
            home = str((row.get("homeTeam") or {}).get("name") or "home")
            away = str((row.get("awayTeam") or {}).get("name") or "away")

            age = self._event_change_age(row, now_ts)
            if age is not None:
                change_ages.append(age)
                if age > self.stale_threshold_sec:
                    stale_ids.append(event_id_str)

            events.append(
                Event(
                    event_id=f"sofascore:{event_id_str}",
                    sport="football",
                    league=league,
                    home=home,
                    away=away,
                    start_time=start_time,
                    markets=market_quotes,
                    sources=("sofascore",),
                    data_quality_score=0.58,
                )
            )
            signatures_initial[event_id_str] = signature

        sample_ids = list(signatures_initial.keys())[: self.sample_check]
        sample_changed = 0
        sample_same = 0
        sample_failed = 0
        for event_id_str in sample_ids:
            try:
                odds_check = self._http_get_json(f"/event/{event_id_str}/odds/1/all")
                _, sig2 = self._build_market_quotes(odds_check)
                if sig2 != signatures_initial[event_id_str]:
                    sample_changed += 1
                else:
                    sample_same += 1
            except SofaScrapeError:
                sample_failed += 1

        stale_count = len(stale_ids)
        distinct_book_counts = [len({m.bookmaker for m in ev.markets}) for ev in events]
        events_with_two_books = sum(1 for c in distinct_book_counts if c >= 2)
        if not events:
            freshness_status = "no_data"
        elif stale_count > (len(events) / 2):
            freshness_status = "stale_risk"
        elif sample_changed > 0 or stale_count == 0:
            freshness_status = "fresh"
        else:
            freshness_status = "unknown"

        max_age = max(change_ages) if change_ages else None
        avg_age = (sum(change_ages) / len(change_ages)) if change_ages else None

        self.last_diagnostics = {
            "source": "sofascore_scraper",
            "fetched_at": now.isoformat(),
            "live_events_seen": len(rows),
            "events_with_h2h": len(events),
            "events_with_at_least_two_books": events_with_two_books,
            "max_distinct_books_per_event": max(distinct_book_counts) if distinct_book_counts else 0,
            "min_distinct_books_per_event": min(distinct_book_counts) if distinct_book_counts else 0,
            "stale_threshold_sec": self.stale_threshold_sec,
            "stale_events_count": stale_count,
            "stale_event_ids": stale_ids[:10],
            "max_change_age_sec": round(max_age, 2) if max_age is not None else None,
            "avg_change_age_sec": round(avg_age, 2) if avg_age is not None else None,
            "sample_checked": len(sample_ids),
            "sample_changed": sample_changed,
            "sample_unchanged": sample_same,
            "sample_failed": sample_failed,
            "freshness_status": freshness_status,
            "base_url_used": self.last_base_url_used,
            "feed_note": (
                "Scraper defaults to live-only quotes to avoid mixing live/prematch prices. "
                "SofaScore web odds usually expose a single upstream feed (sourceId=1), so "
                "true multi-book arbitrage coverage is limited."
            ),
        }
        if events and events_with_two_books == 0:
            self.last_diagnostics["arbitrage_blocker"] = (
                "No event has >=2 distinct bookmakers in scraped feed; arbitrage scan returns 0."
            )

        return events
