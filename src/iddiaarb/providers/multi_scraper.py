from __future__ import annotations

from dataclasses import replace
from typing import Any

from iddiaarb.matcher import merge_similar_events
from iddiaarb.models import Event
from iddiaarb.providers.flashscore_scraper import FlashscoreScrapeError, FlashscoreScraperProvider
from iddiaarb.providers.sofascore_scraper import SofaScrapeError, SofaScoreScraperProvider
from iddiaarb.quality import compute_event_quality


class MultiScraperError(RuntimeError):
    pass


class MultiScraperProvider:
    def __init__(
        self,
        max_events: int = 40,
        include_flashscore: bool = True,
        include_sofascore: bool = True,
        flashscore_geo_ip_code: str = "GB",
        flashscore_geo_ip_subdivision: str = "GBENG",
        flashscore_max_books: int = 8,
        quorum_min_sources: int = 2,
        matcher_confidence: float = 0.80,
    ) -> None:
        self.max_events = max_events
        self.include_flashscore = include_flashscore
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
            raise MultiScraperError("No scraper data available from selected sources")

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
            "sofascore_events": sofa_events,
            "merged_events": len(merged),
            "events_with_quorum": len(quorum_events),
            "quorum_min_sources": self.quorum_min_sources,
            "match_links": len(decisions),
            "warnings": warnings,
        }
        return quorum_events
