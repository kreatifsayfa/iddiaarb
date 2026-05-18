from __future__ import annotations

from dataclasses import replace
from typing import Any

from iddiaarb.matcher import merge_similar_events
from iddiaarb.models import Event
from iddiaarb.providers.betexplorer_scraper import BetExplorerScraperProvider, BetExplorerScrapeError
from iddiaarb.providers.flashscore_scraper import FlashscoreScrapeError, FlashscoreScraperProvider
from iddiaarb.providers.livesport_scraper import (
    LivesportScrapeError,
    build_livesport_provider,
    build_soccer24_provider,
)
from iddiaarb.providers.sofascore_scraper import SofaScrapeError, SofaScoreScraperProvider
from iddiaarb.quality import compute_event_quality


class MultiScraperError(RuntimeError):
    pass


class MultiScraperProvider:
    def __init__(
        self,
        max_events: int = 40,
        include_flashscore: bool = True,
        include_soccer24: bool = True,
        include_livesport: bool = True,
        include_betexplorer: bool = True,
        include_sofascore: bool = False,
        flashscore_geo_ip_code: str = "GB",
        flashscore_geo_ip_subdivision: str = "GBENG",
        flashscore_max_books: int = 8,
        quorum_min_sources: int = 2,
        matcher_confidence: float = 0.80,
    ) -> None:
        self.max_events = max_events
        self.include_flashscore = include_flashscore
        self.include_soccer24 = include_soccer24
        self.include_livesport = include_livesport
        self.include_betexplorer = include_betexplorer
        self.include_sofascore = include_sofascore
        self.flashscore_geo_ip_code = flashscore_geo_ip_code
        self.flashscore_geo_ip_subdivision = flashscore_geo_ip_subdivision
        self.flashscore_max_books = flashscore_max_books
        self.quorum_min_sources = max(1, quorum_min_sources)
        self.matcher_confidence = matcher_confidence
        self.last_diagnostics: dict[str, Any] = {}

    def _prepare_events(self, rows: list[Event], source: str) -> list[Event]:
        prepared: list[Event] = []
        for ev in rows:
            markets = [replace(m, source=source, quality_score=max(m.quality_score, 0.5)) for m in ev.markets]
            prepared.append(
                replace(
                    ev,
                    sources=(source,),
                    data_quality_score=max(ev.data_quality_score, 0.55),
                    markets=markets,
                )
            )
        return prepared

    def load_events(self) -> list[Event]:
        all_events: list[Event] = []
        flash_events = 0
        soccer24_events = 0
        livesport_events = 0
        betexplorer_events = 0
        sofa_events = 0
        warnings: list[str] = []

        if self.include_flashscore:
            flash = FlashscoreScraperProvider(
                max_events=self.max_events,
                geo_ip_code=self.flashscore_geo_ip_code,
                geo_ip_subdivision_code=self.flashscore_geo_ip_subdivision,
                max_bookmakers_per_event=self.flashscore_max_books,
            )
            try:
                rows = flash.load_events()
                flash_events = len(rows)
                all_events.extend(self._prepare_events(rows, "flashscore"))
            except FlashscoreScrapeError as exc:
                warnings.append(f"flashscore_error={exc}")

        if self.include_soccer24:
            soccer24 = build_soccer24_provider(
                max_events=self.max_events,
                geo_ip_code=self.flashscore_geo_ip_code,
                geo_ip_subdivision_code=self.flashscore_geo_ip_subdivision,
                max_bookmakers_per_event=self.flashscore_max_books,
            )
            try:
                rows = soccer24.load_events()
                soccer24_events = len(rows)
                all_events.extend(self._prepare_events(rows, "soccer24"))
            except LivesportScrapeError as exc:
                warnings.append(f"soccer24_error={exc}")

        if self.include_livesport:
            livesport = build_livesport_provider(
                max_events=self.max_events,
                geo_ip_code=self.flashscore_geo_ip_code,
                geo_ip_subdivision_code=self.flashscore_geo_ip_subdivision,
                max_bookmakers_per_event=self.flashscore_max_books,
            )
            try:
                rows = livesport.load_events()
                livesport_events = len(rows)
                all_events.extend(self._prepare_events(rows, "livesport"))
            except LivesportScrapeError as exc:
                warnings.append(f"livesport_error={exc}")

        if self.include_betexplorer:
            betexplorer = BetExplorerScraperProvider(max_events=self.max_events)
            try:
                rows = betexplorer.load_events()
                betexplorer_events = len(rows)
                all_events.extend(self._prepare_events(rows, "betexplorer"))
            except BetExplorerScrapeError as exc:
                warnings.append(f"betexplorer_error={exc}")

        if self.include_sofascore:
            sofa = SofaScoreScraperProvider(
                max_events=self.max_events,
                include_prematch=True,
            )
            try:
                rows = sofa.load_events()
                sofa_events = len(rows)
                all_events.extend(self._prepare_events(rows, "sofascore"))
            except SofaScrapeError as exc:
                warnings.append(f"sofascore_error={exc}")

        if not all_events:
            blocker = (
                "All selected scraper sources failed; see warnings."
                if warnings
                else "No selected scraper sources returned events."
            )
            self.last_diagnostics = {
                "source": "multi_scraper",
                "events_raw_total": 0,
                "flashscore_events": flash_events,
                "soccer24_events": soccer24_events,
                "livesport_events": livesport_events,
                "betexplorer_events": betexplorer_events,
                "sofascore_events": sofa_events,
                "merged_events": 0,
                "events_with_quorum": 0,
                "quorum_min_sources": self.quorum_min_sources,
                "match_links": 0,
                "warnings": warnings,
                "arbitrage_blocker": blocker,
            }
            return []

        merged, decisions = merge_similar_events(
            all_events,
            min_confidence=self.matcher_confidence,
            max_start_delta_minutes=90,
        )

        quorum_events: list[Event] = []
        for ev in merged:
            source_names: set[str] = set()
            for part in ev.event_id.split("|"):
                if ":" in part:
                    source_names.add(part.split(":", 1)[0])
            if not source_names and ev.sources:
                source_names = set(ev.sources)

            quality = compute_event_quality(ev)
            candidate = replace(
                ev,
                sources=tuple(sorted(source_names)) if source_names else ev.sources,
                data_quality_score=max(ev.data_quality_score, quality),
            )
            if len(candidate.sources or ("unknown",)) >= self.quorum_min_sources:
                quorum_events.append(candidate)

        self.last_diagnostics = {
            "source": "multi_scraper",
            "events_raw_total": len(all_events),
            "flashscore_events": flash_events,
            "soccer24_events": soccer24_events,
            "livesport_events": livesport_events,
            "betexplorer_events": betexplorer_events,
            "sofascore_events": sofa_events,
            "merged_events": len(merged),
            "events_with_quorum": len(quorum_events),
            "quorum_min_sources": self.quorum_min_sources,
            "match_links": len(decisions),
            "warnings": warnings,
        }
        return quorum_events
