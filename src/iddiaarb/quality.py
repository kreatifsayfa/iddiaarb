from __future__ import annotations

from iddiaarb.models import Event


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_event_quality(event: Event) -> float:
    if event.data_quality_score > 0:
        return clamp01(event.data_quality_score)

    distinct_books = len({m.bookmaker for m in event.markets})
    market_count = len(event.markets)
    source_count = len(event.sources) if event.sources else 1

    book_score = min(1.0, distinct_books / 6.0)
    market_score = min(1.0, market_count / 12.0)
    source_score = min(1.0, source_count / 2.0)

    # Weighted quality score for ranking/filtering.
    return clamp01((0.55 * book_score) + (0.30 * source_score) + (0.15 * market_score))
