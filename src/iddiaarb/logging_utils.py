from __future__ import annotations

import json
from datetime import datetime, timezone


def log_json(event: str, **fields) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False))
