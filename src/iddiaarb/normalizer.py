from __future__ import annotations


_OUTCOME_ALIASES = {
    "1": "HOME",
    "home": "HOME",
    "ev sahibi": "HOME",
    "x": "DRAW",
    "draw": "DRAW",
    "beraberlik": "DRAW",
    "2": "AWAY",
    "away": "AWAY",
    "deplasman": "AWAY",
    "over": "OVER",
    "ust": "OVER",
    "u": "OVER",
    "under": "UNDER",
    "alt": "UNDER",
    "a": "UNDER",
}


def normalize_outcome_key(raw_key: str, raw_label: str = "") -> str:
    key = (raw_key or "").strip().lower()
    label = (raw_label or "").strip().lower()
    if key in _OUTCOME_ALIASES:
        return _OUTCOME_ALIASES[key]
    if label in _OUTCOME_ALIASES:
        return _OUTCOME_ALIASES[label]
    if key:
        return key.upper()
    return label.upper()

