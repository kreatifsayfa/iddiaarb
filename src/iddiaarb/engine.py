from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, Tuple

from iddiaarb.limits import BookmakerLimit
from iddiaarb.models import ArbitrageOpportunity, Event, MarketQuote, StakePlan
from iddiaarb.quality import compute_event_quality


class ArbitrageEngine:
    def __init__(
        self,
        min_margin_pct: float = 0.0,
        min_distinct_books: int = 2,
        min_event_sources: int = 1,
        min_data_quality: float = 0.0,
        bookmaker_limits: dict[str, BookmakerLimit] | None = None,
        slippage_bps: float = 0.0,
        rejection_rate: float = 0.0,
    ) -> None:
        self.min_margin_pct = min_margin_pct
        self.min_distinct_books = min_distinct_books
        self.min_event_sources = max(1, min_event_sources)
        self.min_data_quality = max(0.0, min(1.0, min_data_quality))
        self.bookmaker_limits = bookmaker_limits or {}
        self.slippage_bps = max(0.0, slippage_bps)
        self.rejection_rate = max(0.0, min(1.0, rejection_rate))

    def scan(self, events: Iterable[Event], bankroll: float) -> list[ArbitrageOpportunity]:
        opportunities: list[ArbitrageOpportunity] = []
        for event in events:
            source_count = len(event.sources) if event.sources else 1
            if source_count < self.min_event_sources:
                continue

            quality = compute_event_quality(event)
            if quality < self.min_data_quality:
                continue

            grouped = self._group_by_market(event.markets)
            for market_key, market_quotes in grouped.items():
                result = self._best_outcomes(market_quotes)
                if not result:
                    continue
                best_odds, best_books = result
                total_implied = sum(1 / odd for odd in best_odds.values())
                if total_implied >= 1:
                    continue

                margin_pct = (1 / total_implied - 1) * 100
                if margin_pct < self.min_margin_pct:
                    continue
                if len(set(best_books.values())) < self.min_distinct_books:
                    continue

                stake_plan = self._build_stake_plan(best_odds, best_books, bankroll)
                if stake_plan is None:
                    continue

                slippage_penalty = (stake_plan.bankroll * self.slippage_bps) / 10000.0
                rejection_penalty = stake_plan.guaranteed_profit * self.rejection_rate
                risk_penalty = slippage_penalty + rejection_penalty
                expected_profit = stake_plan.guaranteed_profit - risk_penalty
                risk_score = max(
                    0.0,
                    min(
                        100.0,
                        (self.rejection_rate * 65.0)
                        + (self.slippage_bps / 4.0)
                        + ((1.0 - quality) * 35.0),
                    ),
                )
                opportunities.append(
                    ArbitrageOpportunity(
                        event_id=event.event_id,
                        event_label=f"{event.home} vs {event.away}",
                        market_key=market_key,
                        outcomes=best_odds,
                        bookmakers=best_books,
                        total_implied_probability=total_implied,
                        margin_pct=margin_pct,
                        stake_plan=stake_plan,
                        expected_profit=expected_profit,
                        risk_penalty=risk_penalty,
                        risk_score=risk_score,
                        source_count=source_count,
                        data_quality_score=quality,
                    )
                )
        opportunities.sort(
            key=lambda x: (x.expected_profit, x.margin_pct, x.data_quality_score), reverse=True
        )
        return opportunities

    def _group_by_market(self, markets: Iterable[MarketQuote]) -> Dict[str, list[MarketQuote]]:
        grouped: Dict[str, list[MarketQuote]] = defaultdict(list)
        for market in markets:
            grouped[market.market_key].append(market)
        return grouped

    def _best_outcomes(
        self, market_quotes: Iterable[MarketQuote]
    ) -> Tuple[Dict[str, float], Dict[str, str]] | None:
        best_odds: Dict[str, float] = {}
        best_books: Dict[str, str] = {}
        outcomes_seen = set()

        for quote in market_quotes:
            for outcome in quote.outcomes:
                outcomes_seen.add(outcome.key)
                effective_odd = self._effective_odd(quote.bookmaker, outcome.odds)
                if outcome.key not in best_odds or effective_odd > best_odds[outcome.key]:
                    best_odds[outcome.key] = effective_odd
                    best_books[outcome.key] = quote.bookmaker

        # Need at least 2 outcomes to define a hedge
        if len(outcomes_seen) < 2:
            return None
        if len(best_odds) != len(outcomes_seen):
            return None
        return best_odds, best_books

    def _effective_odd(self, bookmaker: str, odd: float) -> float:
        limit = self.bookmaker_limits.get(bookmaker)
        if not limit:
            return odd
        commission = max(0.0, min(100.0, limit.commission_pct))
        return 1 + ((odd - 1) * (1 - (commission / 100.0)))

    def _build_stake_plan(
        self, odds: Dict[str, float], books: Dict[str, str], bankroll: float
    ) -> StakePlan | None:
        implied = {k: 1 / v for k, v in odds.items()}
        total_implied = sum(implied.values())
        allocations = {k: bankroll * (p / total_implied) for k, p in implied.items()}
        used_bankroll = bankroll
        constraints_applied = False
        notes: list[str] = []

        scale_caps: list[float] = []
        for outcome_key, stake in allocations.items():
            bookmaker = books.get(outcome_key, "")
            limit = self.bookmaker_limits.get(bookmaker)
            if not limit:
                continue
            cap = limit.cap_for_odd(odds[outcome_key])
            if cap is not None and stake > cap:
                constraints_applied = True
                notes.append(f"{bookmaker}:{outcome_key} cap={cap:.2f}")
                if stake > 0:
                    scale_caps.append(cap / stake)
        if scale_caps:
            scale = min(scale_caps)
            scale = max(0.0, min(1.0, scale))
            allocations = {k: v * scale for k, v in allocations.items()}
            used_bankroll = sum(allocations.values())

        for outcome_key, stake in allocations.items():
            bookmaker = books.get(outcome_key, "")
            limit = self.bookmaker_limits.get(bookmaker)
            if not limit:
                continue
            if stake < max(0.0, limit.min_stake):
                return None

        guaranteed_return = min(allocations[k] * odds[k] for k in allocations)
        guaranteed_profit = guaranteed_return - used_bankroll
        guaranteed_profit_pct = (guaranteed_profit / used_bankroll) * 100 if used_bankroll > 0 else 0.0
        return StakePlan(
            requested_bankroll=bankroll,
            bankroll=used_bankroll,
            total_implied_probability=total_implied,
            allocations=allocations,
            guaranteed_return=guaranteed_return,
            guaranteed_profit=guaranteed_profit,
            guaranteed_profit_pct=guaranteed_profit_pct,
            constraints_applied=constraints_applied,
            constraints_note="; ".join(notes),
        )
