from __future__ import annotations

from typing import Protocol

from iddiaarb.models import Event


class OddsProvider(Protocol):
    def load_events(self) -> list[Event]:
        ...

