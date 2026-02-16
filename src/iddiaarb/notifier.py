from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def _post_json(url: str, payload: dict, timeout_sec: int = 10) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url=url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout_sec):
        return


@dataclass
class WebhookNotifier:
    webhook_url: str
    timeout_sec: int = 10

    def send(self, title: str, lines: list[str]) -> None:
        payload = {"title": title, "message": "\n".join(lines), "lines": lines}
        _post_json(self.webhook_url, payload=payload, timeout_sec=self.timeout_sec)


@dataclass
class TelegramNotifier:
    token: str
    chat_id: str
    timeout_sec: int = 10

    def send(self, title: str, lines: list[str]) -> None:
        text = title
        if lines:
            text = f"{title}\n" + "\n".join(lines)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        _post_json(url, payload=payload, timeout_sec=self.timeout_sec)


def build_alert_lines(opportunities: list[dict], top_n: int = 5) -> list[str]:
    lines: list[str] = []
    for idx, opp in enumerate(opportunities[:top_n], start=1):
        lines.append(
            f"{idx}) {opp['event']} | {opp['market']} | margin={opp['margin_pct']:.2f}% | profit={opp['profit']:.2f}"
        )
    return lines


def send_alerts(
    opportunities: list[dict],
    webhook_url: str | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    dedupe_state_path: str = ".state/alerts_state.json",
    min_margin_delta: float = 0.15,
) -> list[str]:
    filtered = _filter_meaningful_alerts(
        opportunities,
        state_path=dedupe_state_path,
        min_margin_delta=min_margin_delta,
    )
    if not filtered:
        return []
    lines = build_alert_lines(filtered)
    sent: list[str] = []
    title = f"iddiaarb: {len(filtered)} opportunities"

    if webhook_url:
        WebhookNotifier(webhook_url=webhook_url).send(title=title, lines=lines)
        sent.append("webhook")
    if telegram_token and telegram_chat_id:
        TelegramNotifier(token=telegram_token, chat_id=telegram_chat_id).send(
            title=title, lines=lines
        )
        sent.append("telegram")
    return sent


def safe_send_alerts(
    opportunities: list[dict],
    webhook_url: str | None = None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    dedupe_state_path: str = ".state/alerts_state.json",
    min_margin_delta: float = 0.15,
) -> tuple[list[str], str | None]:
    try:
        return (
            send_alerts(
                opportunities=opportunities,
                webhook_url=webhook_url,
                telegram_token=telegram_token,
                telegram_chat_id=telegram_chat_id,
                dedupe_state_path=dedupe_state_path,
                min_margin_delta=min_margin_delta,
            ),
            None,
        )
    except URLError as exc:
        return [], f"Alert network error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return [], f"Alert error: {exc}"


def _opp_key(opp: dict) -> str:
    return "|".join(
        [
            str(opp.get("event_id", "")),
            str(opp.get("market", "")),
            json.dumps(opp.get("bookmakers", {}), sort_keys=True),
        ]
    )


def _read_state(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _filter_meaningful_alerts(
    opportunities: list[dict],
    state_path: str,
    min_margin_delta: float,
) -> list[dict]:
    now = int(datetime.now(timezone.utc).timestamp())
    prev_state = _read_state(state_path)
    next_state = dict(prev_state)
    selected: list[dict] = []
    for opp in opportunities:
        key = _opp_key(opp)
        margin = float(opp.get("margin_pct", 0.0))
        prev = prev_state.get(key)
        should_send = False
        if not isinstance(prev, dict):
            should_send = True
        else:
            prev_margin = float(prev.get("margin_pct", 0.0))
            if abs(margin - prev_margin) >= min_margin_delta:
                should_send = True
        if should_send:
            selected.append(opp)
        next_state[key] = {"margin_pct": margin, "ts": now}

    # Keep state bounded.
    if len(next_state) > 3000:
        trimmed = sorted(
            next_state.items(),
            key=lambda kv: int(kv[1].get("ts", 0)) if isinstance(kv[1], dict) else 0,
            reverse=True,
        )[:3000]
        next_state = dict(trimmed)
    _write_state(state_path, next_state)
    return selected
