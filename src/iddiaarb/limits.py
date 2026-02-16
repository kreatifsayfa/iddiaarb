from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookmakerLimit:
    min_stake: float = 0.0
    max_stake: float | None = None
    max_payout: float | None = None
    commission_pct: float = 0.0

    def cap_for_odd(self, odd: float) -> float | None:
        caps: list[float] = []
        if self.max_stake is not None and self.max_stake > 0:
            caps.append(self.max_stake)
        if self.max_payout is not None and self.max_payout > 0 and odd > 0:
            caps.append(self.max_payout / odd)
        if not caps:
            return None
        return min(caps)
