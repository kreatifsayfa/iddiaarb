from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher

from iddiaarb.models import Event, MarketQuote

_STOPWORDS = {
    "fc",
    "fk",
    "sk",
    "club",
    "cf",
    "sc",
    "as",
    "ac",
    "if",
    "ca",
    "cd",
    "cs",
    "de",
    "del",
    "la",
    "las",
    "los",
    "y",
    "club",
    "atletico",
    "athletico",
    "deportivo",
}


def _norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    tokens = [t for t in value.split() if t and len(t) > 1 and t not in _STOPWORDS]
    return " ".join(tokens)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _token_set_ratio(a: str, b: str) -> float:
    sa = set(a.split())
    sb = set(b.split())
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def event_confidence(
    left: Event, right: Event, max_start_delta_minutes: int = 120
) -> float:
    l_home = _norm_name(left.home)
    l_away = _norm_name(left.away)
    r_home = _norm_name(right.home)
    r_away = _norm_name(right.away)

    straight = (_ratio(l_home, r_home) + _ratio(l_away, r_away)) / 2
    swapped = (_ratio(l_home, r_away) + _ratio(l_away, r_home)) / 2
    straight_token = (_token_set_ratio(l_home, r_home) + _token_set_ratio(l_away, r_away)) / 2
    swapped_token = (_token_set_ratio(l_home, r_away) + _token_set_ratio(l_away, r_home)) / 2
    name_score = max(
        (0.72 * straight) + (0.28 * straight_token),
        ((0.72 * swapped) + (0.28 * swapped_token)) * 0.95,
    )

    delta = abs((left.start_time - right.start_time).total_seconds()) / 60.0
    if delta > max_start_delta_minutes:
        time_score = 0.0
    else:
        time_score = 1 - (delta / max_start_delta_minutes)

    league_score = _ratio(_norm_name(left.league), _norm_name(right.league))
    sport_score = 1.0 if left.sport == right.sport else 0.0
    score = (0.64 * name_score) + (0.24 * time_score) + (0.07 * league_score) + (0.05 * sport_score)
    return max(0.0, min(1.0, score))


@dataclass(frozen=True)
class MatchDecision:
    source_event_id: str
    target_event_id: str
    confidence: float


def merge_similar_events(
    events: list[Event],
    min_confidence: float = 0.86,
    max_start_delta_minutes: int = 120,
) -> tuple[list[Event], list[MatchDecision]]:
    if not events:
        return [], []

    ordered = sorted(events, key=lambda e: e.start_time)
    merged: list[Event] = []
    decisions: list[MatchDecision] = []

    for event in ordered:
        best_idx = -1
        best_score = -1.0

        for idx, candidate in enumerate(merged):
            if abs(event.start_time - candidate.start_time) > timedelta(
                minutes=max_start_delta_minutes
            ):
                continue
            score = event_confidence(event, candidate, max_start_delta_minutes)
            if score > best_score:
                best_idx = idx
                best_score = score

        if best_idx >= 0 and best_score >= min_confidence:
            prior = merged[best_idx]
            combined_markets: list[MarketQuote] = [*prior.markets, *event.markets]
            merged_event = Event(
                event_id=f"{prior.event_id}|{event.event_id}",
                sport=prior.sport,
                league=prior.league,
                home=prior.home,
                away=prior.away,
                start_time=prior.start_time,
                markets=combined_markets,
                sources=tuple(sorted(set(prior.sources) | set(event.sources))),
                data_quality_score=max(prior.data_quality_score, event.data_quality_score),
            )
            merged[best_idx] = merged_event
            decisions.append(
                MatchDecision(
                    source_event_id=event.event_id,
                    target_event_id=prior.event_id,
                    confidence=best_score,
                )
            )
        else:
            merged.append(event)

    return merged, decisions
