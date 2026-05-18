from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from iddiaarb.models import Event, MarketQuote, OutcomeQuote


def _find_curl_binary() -> str:
    """Find the curl binary on the system, cross-platform."""
    if os.name == "nt":
        # Windows: prefer curl.exe
        found = shutil.which("curl.exe") or shutil.which("curl")
    else:
        # Linux/Mac: prefer curl
        found = shutil.which("curl")
    return found or "curl"


class FlashscoreScrapeError(RuntimeError):
    pass


class FlashscoreScraperProvider:
    FEED_URL = "https://2.flashscore.ninja/2/x/feed/f_1_0_3_en_1"
    ODDS_URL = "https://global.ds.lsapp.eu/odds/pq_graphql"
    PROJECT_ID = "2"

    def __init__(
        self,
        max_events: int = 40,
        timeout_sec: int = 20,
        geo_ip_code: str = "GB",
        geo_ip_subdivision_code: str = "GBENG",
        max_bookmakers_per_event: int = 8,
        request_pause_sec: float = 0.04,
        prefer_curl: bool = True,
        retries: int = 2,
    ) -> None:
        self.max_events = max(1, max_events)
        self.timeout_sec = max(5, timeout_sec)
        self.geo_ip_code = (geo_ip_code or "GB").strip().upper()
        self.geo_ip_subdivision_code = (geo_ip_subdivision_code or "GBENG").strip().upper()
        self.max_bookmakers_per_event = max(1, max_bookmakers_per_event)
        self.request_pause_sec = max(0.0, request_pause_sec)
        self.prefer_curl = prefer_curl
        self.retries = max(0, retries)
        self.last_diagnostics: dict[str, Any] = {}
        self.last_odds_url_used = self.ODDS_URL

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
            raise FlashscoreScrapeError(f"curl not found in PATH. Install curl or use urllib mode.") from exc
        if proc.returncode != 0:
            raise FlashscoreScrapeError(
                f"curl failed ({proc.returncode}) for {url}: {proc.stderr.strip()}"
            )
        out = proc.stdout
        if not out:
            raise FlashscoreScrapeError(f"Empty response from {url}")
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
                if self.prefer_curl:
                    # curl modunda hata alinirsa bir sonraki denemede urllib fallback dene
                    try:
                        return self._http_get_text_urllib(url, headers)
                    except Exception as exc2:  # noqa: BLE001
                        last_err = exc2
                else:
                    # urllib modunda hata alinirsa curl fallback dene
                    try:
                        return self._http_get_text_via_curl(url, headers)
                    except Exception as exc2:  # noqa: BLE001
                        last_err = exc2

                if attempt < self.retries:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                break
        raise FlashscoreScrapeError(f"HTTP fetch failed for {url}: {last_err}") from last_err

    def _http_get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        raw = self._http_get_text(url, headers=headers)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FlashscoreScrapeError(f"Invalid JSON from {url}") from exc
        if not isinstance(payload, dict):
            raise FlashscoreScrapeError(
                f"Unexpected payload type from {url}: {type(payload).__name__}"
            )
        return payload

    def _odds_url_candidates(self, params: str) -> list[str]:
        # GitHub'daki sahada kullanilan varyantlara gore fallback host/path listesi:
        # - global.ds.lsapp.eu/odds/pq_graphql (mevcut)
        # - {project}.ds.lsapp.eu/pq_graphql
        # - {project}.ds.lsapp.eu/odds/pq_graphql
        candidates = [
            f"{self.ODDS_URL}?{params}",
            f"https://{self.PROJECT_ID}.ds.lsapp.eu/pq_graphql?{params}",
            f"https://{self.PROJECT_ID}.ds.lsapp.eu/odds/pq_graphql?{params}",
        ]
        # Sira korunsun ama duplicate URL olursa tekilleÅŸtir.
        seen: set[str] = set()
        out: list[str] = []
        for url in candidates:
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    def _http_get_json_with_fallback(self, urls: list[str], headers: dict[str, str]) -> dict[str, Any]:
        last_err: Exception | None = None
        for url in urls:
            try:
                payload = self._http_get_json(url, headers=headers)
                self.last_odds_url_used = url.split("?", 1)[0]
                return payload
            except FlashscoreScrapeError as exc:
                last_err = exc
                continue
        raise FlashscoreScrapeError(
            f"All odds endpoints failed ({len(urls)} urls): {last_err}"
        ) from last_err

    def _fetch_feed_events(self) -> list[dict[str, Any]]:
        raw = self._http_get_text(
            self.FEED_URL,
            headers={
                "Accept": "text/plain,*/*",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.flashscore.com/",
                "x-fsign": "SW9D1eZo",
                "x-geoip": "1",
            },
        )

        sep = chr(172)  # Flashscore feed field delimiter
        if sep not in raw:
            snippet = raw[:120].strip().replace("\n", " ")
            raise FlashscoreScrapeError(
                f"Unexpected feed response from {self.FEED_URL} (no delimiter): {snippet!r}"
            )
        tokens = raw.split(sep)
        events: list[dict[str, Any]] = []
        current_league = "Flashscore Football"
        current_event: dict[str, Any] | None = None

        def flush_event() -> None:
            nonlocal current_event
            if not current_event:
                return
            event_id = str(current_event.get("event_id") or "").strip()
            home = str(current_event.get("home") or "").strip()
            away = str(current_event.get("away") or "").strip()
            start_ts = current_event.get("start_ts")
            if (
                len(event_id) >= 6
                and home
                and away
                and isinstance(start_ts, int)
                and start_ts > 0
            ):
                events.append(current_event)
            current_event = None

        mojibake_delim = chr(195) + chr(183)  # "Ã·"
        for token in tokens:
            if mojibake_delim in token:
                key, value = token.split(mojibake_delim, 1)
            elif "÷" in token:
                key, value = token.split("÷", 1)
            else:
                continue

            if key == "~ZA":
                flush_event()
                league_name = value.strip()
                if league_name:
                    current_league = league_name
                continue

            if key == "~AA":
                flush_event()
                current_event = {
                    "event_id": value.strip(),
                    "league": current_league,
                    "start_ts": None,
                    "home": "",
                    "away": "",
                }
                continue

            if not current_event:
                continue

            if key == "AD":
                try:
                    current_event["start_ts"] = int(value.strip())
                except ValueError:
                    current_event["start_ts"] = None
            elif key == "AE":
                current_event["home"] = value.strip()
            elif key == "AF":
                current_event["away"] = value.strip()

        flush_event()
        return events

    def _fetch_menu(self, event_id: str) -> dict[str, Any]:
        params = urlencode(
            {
                "_hash": "pobtm",
                "eventId": event_id,
                "projectId": self.PROJECT_ID,
                "geoIpCode": self.geo_ip_code,
                "geoIpSubdivisionCode": self.geo_ip_subdivision_code,
            }
        )
        return self._http_get_json_with_fallback(
            self._odds_url_candidates(params),
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://www.flashscore.com",
                "Referer": "https://www.flashscore.com/",
            },
        )

    @staticmethod
    def _extract_h2h_from_payload(payload: dict[str, Any]) -> tuple[float, float, float] | None:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        row: dict[str, Any] | None = None
        for key in ("findPrematchOddsForBookmaker", "findPrematchOdds"):
            cand = data.get(key)
            if isinstance(cand, dict):
                row = cand
                break
        if not row:
            return None

        home_raw = ((row.get("home") or {}).get("value")) if isinstance(row.get("home"), dict) else None
        draw_raw = ((row.get("draw") or {}).get("value")) if isinstance(row.get("draw"), dict) else None
        away_raw = ((row.get("away") or {}).get("value")) if isinstance(row.get("away"), dict) else None

        try:
            home = float(home_raw)
            draw = float(draw_raw)
            away = float(away_raw)
        except (TypeError, ValueError):
            return None
        if home <= 1 or draw <= 1 or away <= 1:
            return None
        return home, draw, away

    def _extract_home_draw_away_books(self, menu: dict[str, Any]) -> list[tuple[int, str]]:
        root = menu.get("data", {}).get("getPrematchOddsBettingTypeMenu", {})
        settings = root.get("settings", {})
        items = root.get("items")

        if not isinstance(items, list):
            return []

        target_item: dict[str, Any] | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            if not item.get("isActive"):
                continue
            if item.get("bettingType") != "HOME_DRAW_AWAY":
                continue
            if item.get("bettingScope") != "FULL_TIME":
                continue
            target_item = item
            break
        if not target_item:
            return []

        book_name_map: dict[int, str] = {}
        settings_books = settings.get("bookmakers")
        if isinstance(settings_books, list):
            for row in settings_books:
                if not isinstance(row, dict):
                    continue
                bookmaker = row.get("bookmaker")
                if not isinstance(bookmaker, dict):
                    continue
                book_id = bookmaker.get("id")
                if not isinstance(book_id, int):
                    continue
                name = str(bookmaker.get("name") or f"bookmaker_{book_id}").strip()
                book_name_map[book_id] = name

        out: list[tuple[int, str]] = []
        seen: set[int] = set()
        for raw_id in target_item.get("bookmakerIds") or []:
            if not isinstance(raw_id, int):
                continue
            if raw_id in seen:
                continue
            name = book_name_map.get(raw_id)
            if not name:
                continue
            seen.add(raw_id)
            out.append((raw_id, name))
            if len(out) >= self.max_bookmakers_per_event:
                break

        return out

    def _fetch_h2h_for_bookmaker(
        self,
        event_id: str,
        bookmaker_id: int,
    ) -> tuple[float, float, float] | None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://www.flashscore.com",
            "Referer": "https://www.flashscore.com/",
        }

        # 1) primary hash (mevcut)
        primary = urlencode(
            {
                "_hash": "ope2",
                "eventId": event_id,
                "bookmakerId": str(bookmaker_id),
                "betType": "HOME_DRAW_AWAY",
                "betScope": "FULL_TIME",
            }
        )
        parsed: tuple[float, float, float] | None = None
        try:
            payload = self._http_get_json_with_fallback(self._odds_url_candidates(primary), headers=headers)
            parsed = self._extract_h2h_from_payload(payload)
            if parsed:
                return parsed
        except FlashscoreScrapeError:
            parsed = None

        # 2) GitHub'da gorulen legacy hash fallback
        fallback = urlencode(
            {
                "_hash": "ope",
                "eventId": event_id,
                "projectId": self.PROJECT_ID,
                "geoIpCode": self.geo_ip_code,
                "geoIpSubdivisionCode": self.geo_ip_subdivision_code,
            }
        )
        payload2 = self._http_get_json_with_fallback(self._odds_url_candidates(fallback), headers=headers)
        return self._extract_h2h_from_payload(payload2)

    def load_events(self) -> list[Event]:
        now = datetime.now(timezone.utc)
        feed_rows = self._fetch_feed_events()
        if not feed_rows:
            self.last_diagnostics = {
                "source": "flashscore_scraper",
                "fetched_at": now.isoformat(),
                "feed_events_seen": 0,
                "events_attempted": 0,
                "events_with_h2h": 0,
                "events_with_at_least_two_books": 0,
                "max_distinct_books_per_event": 0,
                "min_distinct_books_per_event": 0,
                "menu_failed": 0,
                "odds_failed": 0,
                "geo_ip_code": self.geo_ip_code,
                "geo_ip_subdivision_code": self.geo_ip_subdivision_code,
                "arbitrage_blocker": "Flashscore feed parsed 0 events.",
            }
            return []

        events: list[Event] = []
        menu_failed = 0
        odds_failed = 0
        books_per_event: list[int] = []
        # Feed siralamasinda bircok eventte odds menu bos gelebiliyor.
        # Bu nedenle ilk N event ile sinirlamak yerine, makul bir pencere tarayip
        # h2h bulunan event sayisi max_events olunca duruyoruz.
        scan_limit = min(len(feed_rows), max(20, self.max_events * 4))
        used_rows = feed_rows[:scan_limit]
        rows_scanned = 0

        for row in used_rows:
            rows_scanned += 1
            event_id = str(row["event_id"])
            try:
                menu = self._fetch_menu(event_id)
            except FlashscoreScrapeError:
                menu_failed += 1
                continue

            books = self._extract_home_draw_away_books(menu)
            if not books:
                continue

            markets: list[MarketQuote] = []
            for book_id, book_name in books:
                try:
                    prices = self._fetch_h2h_for_bookmaker(event_id, book_id)
                except FlashscoreScrapeError:
                    odds_failed += 1
                    continue
                if not prices:
                    continue
                home, draw, away = prices
                markets.append(
                    MarketQuote(
                        bookmaker=book_name,
                        market_key="h2h_prematch",
                        outcomes=[
                            OutcomeQuote(key="HOME", label="1", odds=home),
                            OutcomeQuote(key="DRAW", label="X", odds=draw),
                            OutcomeQuote(key="AWAY", label="2", odds=away),
                        ],
                        source="flashscore",
                        quality_score=0.75,
                    )
                )
                if self.request_pause_sec > 0:
                    time.sleep(self.request_pause_sec)

            if not markets:
                continue

            start_time = datetime.fromtimestamp(int(row["start_ts"]), tz=timezone.utc)
            events.append(
                Event(
                    event_id=f"flashscore:{event_id}",
                    sport="football",
                    league=str(row.get("league") or "Flashscore Football"),
                    home=str(row["home"]),
                    away=str(row["away"]),
                    start_time=start_time,
                    markets=markets,
                    sources=("flashscore",),
                    data_quality_score=0.74,
                )
            )
            books_per_event.append(len({m.bookmaker for m in markets}))

            if self.request_pause_sec > 0:
                time.sleep(self.request_pause_sec)
            if len(events) >= self.max_events:
                break

        events_with_two_books = sum(1 for c in books_per_event if c >= 2)
        self.last_diagnostics = {
            "source": "flashscore_scraper",
            "fetched_at": now.isoformat(),
            "feed_events_seen": len(feed_rows),
            "events_attempted": rows_scanned,
            "feed_scan_limit": scan_limit,
            "events_with_h2h": len(events),
            "events_with_at_least_two_books": events_with_two_books,
            "max_distinct_books_per_event": max(books_per_event) if books_per_event else 0,
            "min_distinct_books_per_event": min(books_per_event) if books_per_event else 0,
            "menu_failed": menu_failed,
            "odds_failed": odds_failed,
            "geo_ip_code": self.geo_ip_code,
            "geo_ip_subdivision_code": self.geo_ip_subdivision_code,
            "odds_url_used": self.last_odds_url_used,
            "feed_note": (
                "Bookmaker coverage depends on geoIpCode/geoIpSubdivisionCode. "
                "For broader coverage use markets like GB/GBENG, US/USNJ, BR/BRSP."
            ),
        }
        if events and events_with_two_books == 0:
            self.last_diagnostics["arbitrage_blocker"] = (
                "No event has >=2 distinct bookmakers for selected geo; adjust geo settings."
            )
        return events

