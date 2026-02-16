from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iddiaarb.engine import ArbitrageEngine
from iddiaarb.history import backtest_report, save_run
from iddiaarb.limits import BookmakerLimit
from iddiaarb.notifier import safe_send_alerts
from iddiaarb.providers import (
    FlashscoreScrapeError,
    FlashscoreScraperProvider,
    MultiScraperError,
    MultiScraperProvider,
    SofaScrapeError,
    SofaScoreScraperProvider,
)


def _add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Total bankroll")
    parser.add_argument("--min-margin", type=float, default=0.0, help="Minimum margin percentage")
    parser.add_argument(
        "--min-data-quality",
        type=float,
        default=0.0,
        help="Minimum event data quality score [0-1]",
    )
    parser.add_argument(
        "--min-source-quorum",
        type=int,
        default=1,
        help="Minimum number of data sources required per event",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=0.0,
        help="Expected slippage in basis points (risk penalty)",
    )
    parser.add_argument(
        "--rejection-rate",
        type=float,
        default=0.0,
        help="Expected rejection rate [0-1] (risk penalty)",
    )
    parser.add_argument("--limits-file", help="JSON file with bookmaker limits/commission")
    parser.add_argument("--history-db", default="data/iddiaarb_history.db", help="SQLite history db path")
    parser.add_argument(
        "--disable-history",
        action="store_true",
        help="Disable storing scan results into history database",
    )
    parser.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    parser.add_argument("--webhook-url", help="Webhook URL for alerts")
    parser.add_argument("--telegram-token", help="Telegram bot token")
    parser.add_argument("--telegram-chat-id", help="Telegram chat id")
    parser.add_argument(
        "--alert-min-margin-delta",
        type=float,
        default=0.15,
        help="Notify only if margin changed by at least this value",
    )
    parser.add_argument("--log-json-file", default="", help="Write structured json logs to this file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iddiaarb", description="Iddia arbitrage scanner")
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape-scan", help="Scrape football odds and run arbitrage scan")
    scrape.add_argument(
        "--scraper-source",
        choices=["flashscore", "sofascore", "multi"],
        default="flashscore",
        help="Scraper backend",
    )
    scrape.add_argument("--max-events", type=int, default=40, help="Max events to scrape")
    scrape.add_argument(
        "--stale-threshold-sec",
        type=int,
        default=900,
        help="SofaScore stale threshold",
    )
    scrape.add_argument("--sample-check", type=int, default=5, help="SofaScore sample re-check size")
    scrape.add_argument(
        "--include-prematch",
        action="store_true",
        help="SofaScore: include prematch lines",
    )
    scrape.add_argument("--geo-ip-code", default="GB", help="Flashscore geo code")
    scrape.add_argument("--geo-ip-subdivision", default="GBENG", help="Flashscore subdivision")
    scrape.add_argument(
        "--max-bookmakers-per-event",
        type=int,
        default=8,
        help="Flashscore max bookmakers per event",
    )
    scrape.add_argument(
        "--quorum-min-sources",
        type=int,
        default=2,
        help="Multi-scraper source quorum filter",
    )
    _add_scan_args(scrape)

    history_cmd = sub.add_parser("history-report", help="Backtest report from stored scan history")
    history_cmd.add_argument("--history-db", default="data/iddiaarb_history.db", help="SQLite history db path")
    history_cmd.add_argument("--last-runs", type=int, default=100, help="Number of runs to include")
    history_cmd.add_argument("--format", choices=["table", "json"], default="table", help="Output format")

    health = sub.add_parser("health-check", help="Basic service health check")
    health.add_argument("--check-scraper", action="store_true", help="Run quick live scraper smoke check")
    health.add_argument("--format", choices=["table", "json"], default="table", help="Output format")

    return parser


def _load_bookmaker_limits(path: str | None) -> dict[str, BookmakerLimit]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    table = payload.get("bookmakers") if isinstance(payload, dict) and "bookmakers" in payload else payload
    if not isinstance(table, dict):
        raise SystemExit("Invalid limits file format. Expect object or {bookmakers:{...}}")
    out: dict[str, BookmakerLimit] = {}
    for book_name, row in table.items():
        if not isinstance(row, dict):
            continue
        out[str(book_name)] = BookmakerLimit(
            min_stake=float(row.get("min_stake", 0.0)),
            max_stake=float(row["max_stake"]) if row.get("max_stake") is not None else None,
            max_payout=float(row["max_payout"]) if row.get("max_payout") is not None else None,
            commission_pct=float(row.get("commission_pct", 0.0)),
        )
    return out


def _append_log(path: str, event: str, fields: dict[str, Any]) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    payload.update(fields)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def render_table(opps: list[dict]) -> str:
    lines = []
    for idx, opp in enumerate(opps, start=1):
        lines.append(
            f"[{idx}] {opp['event']} | market={opp['market']} | marj={opp['margin_pct']:.2f}% | risk={opp['risk_score']:.1f}"
        )
        lines.append(
            f"  implied={opp['implied']:.4f} | used_bankroll={opp['bankroll']:.2f} | expected_profit={opp['expected_profit']:.2f}"
        )
        for outcome, odd in opp["outcomes"].items():
            book = opp["bookmakers"][outcome]
            stake = opp["stakes"][outcome]
            lines.append(f"  - {outcome}: odd={odd:.2f}, book={book}, stake={stake:.2f}")
        if opp.get("constraints_note"):
            lines.append(f"  constraints: {opp['constraints_note']}")
    if not lines:
        return "Arbitraj bulunamadi."
    return "\n".join(lines)


def _to_payload(opportunities: list) -> list[dict]:
    payload = []
    for opp in opportunities:
        payload.append(
            {
                "event_id": opp.event_id,
                "event": opp.event_label,
                "market": opp.market_key,
                "outcomes": opp.outcomes,
                "bookmakers": opp.bookmakers,
                "implied": opp.total_implied_probability,
                "margin_pct": opp.margin_pct,
                "requested_bankroll": opp.stake_plan.requested_bankroll,
                "bankroll": opp.stake_plan.bankroll,
                "stakes": opp.stake_plan.allocations,
                "guaranteed_return": opp.stake_plan.guaranteed_return,
                "profit": opp.stake_plan.guaranteed_profit,
                "profit_pct": opp.stake_plan.guaranteed_profit_pct,
                "expected_profit": opp.expected_profit,
                "risk_penalty": opp.risk_penalty,
                "risk_score": opp.risk_score,
                "source_count": opp.source_count,
                "data_quality_score": opp.data_quality_score,
                "constraints_applied": opp.stake_plan.constraints_applied,
                "constraints_note": opp.stake_plan.constraints_note,
            }
        )
    return payload


def _render_payload(payload: list[dict], output_format: str, meta: dict | None = None) -> None:
    if output_format == "json":
        print(json.dumps({"count": len(payload), "opportunities": payload, "meta": meta or {}}, indent=2))
    else:
        print(render_table(payload))


def _run_events(
    events: list,
    args: argparse.Namespace,
    match_info: str | None = None,
    extra_meta: dict | None = None,
) -> int:
    _append_log(
        getattr(args, "log_json_file", ""),
        "scan_started",
        {"command": str(args.command), "events_in": len(events)},
    )
    limits = _load_bookmaker_limits(getattr(args, "limits_file", None))
    engine = ArbitrageEngine(
        min_margin_pct=args.min_margin,
        min_distinct_books=2,
        min_event_sources=max(1, int(args.min_source_quorum)),
        min_data_quality=float(args.min_data_quality),
        bookmaker_limits=limits,
        slippage_bps=float(args.slippage_bps),
        rejection_rate=float(args.rejection_rate),
    )
    opportunities = engine.scan(events=events, bankroll=args.bankroll)
    payload = _to_payload(opportunities)

    channels, err = safe_send_alerts(
        opportunities=payload,
        webhook_url=args.webhook_url,
        telegram_token=args.telegram_token,
        telegram_chat_id=args.telegram_chat_id,
        min_margin_delta=args.alert_min_margin_delta,
    )

    meta: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_in": len(events),
    }
    if extra_meta:
        meta.update(extra_meta)
    if match_info:
        meta["match_info"] = match_info
    if err:
        meta["alert_error"] = err
    elif channels:
        meta["alerts_sent"] = channels

    if not getattr(args, "disable_history", False):
        run_id = save_run(
            db_path=args.history_db,
            command=str(args.command),
            meta=meta,
            opportunities=payload,
        )
        meta["history_run_id"] = run_id
    _append_log(
        getattr(args, "log_json_file", ""),
        "scan_finished",
        {
            "command": str(args.command),
            "events_in": len(events),
            "opportunity_count": len(payload),
            "meta": meta,
        },
    )

    _render_payload(payload, args.format, meta=meta)
    if args.format == "table":
        if match_info:
            print(match_info)
        if err:
            print(err)
        elif channels:
            print(f"Alerts sent via: {', '.join(channels)}")
    return 0


def run_scrape_scan(args: argparse.Namespace) -> int:
    source = str(args.scraper_source)
    if source == "flashscore":
        provider = FlashscoreScraperProvider(
            max_events=args.max_events,
            geo_ip_code=args.geo_ip_code,
            geo_ip_subdivision_code=args.geo_ip_subdivision,
            max_bookmakers_per_event=args.max_bookmakers_per_event,
        )
        try:
            events = provider.load_events()
        except FlashscoreScrapeError as exc:
            raise SystemExit(f"Scraper error: {exc}") from exc
    elif source == "sofascore":
        provider = SofaScoreScraperProvider(
            max_events=args.max_events,
            stale_threshold_sec=args.stale_threshold_sec,
            sample_check=args.sample_check,
            include_prematch=args.include_prematch,
        )
        try:
            events = provider.load_events()
        except SofaScrapeError as exc:
            raise SystemExit(f"Scraper error: {exc}") from exc
    else:
        provider = MultiScraperProvider(
            max_events=args.max_events,
            include_flashscore=True,
            include_sofascore=True,
            flashscore_geo_ip_code=args.geo_ip_code,
            flashscore_geo_ip_subdivision=args.geo_ip_subdivision,
            flashscore_max_books=args.max_bookmakers_per_event,
            quorum_min_sources=args.quorum_min_sources,
        )
        try:
            events = provider.load_events()
        except MultiScraperError as exc:
            raise SystemExit(f"Scraper error: {exc}") from exc

    extra_meta = provider.last_diagnostics or {}
    if args.format == "table" and extra_meta:
        print(
            "Scraper diagnostics:"
            f" source={source}, events={extra_meta.get('events_with_h2h', extra_meta.get('events_with_quorum', 0))},"
            f" max_books={extra_meta.get('max_distinct_books_per_event', 'n/a')}"
        )
    return _run_events(events, args, extra_meta=extra_meta)


def run_history_report(args: argparse.Namespace) -> int:
    report = backtest_report(args.history_db, last_runs=args.last_runs)
    payload = {
        "runs": report.runs,
        "total_records": report.total_records,
        "unique_fingerprints": report.unique_fingerprints,
        "avg_margin_pct": report.avg_margin_pct,
        "avg_profit": report.avg_profit,
        "reappeared_next_run_ratio": report.reappeared_next_run_ratio,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print("History report")
        for k, v in payload.items():
            print(f"{k}: {v}")
    return 0


def run_health_check(args: argparse.Namespace) -> int:
    result: dict[str, Any] = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
    }
    if args.check_scraper:
        provider = FlashscoreScraperProvider(max_events=3, geo_ip_code="GB", geo_ip_subdivision_code="GBENG")
        try:
            events = provider.load_events()
            result["scraper_events"] = len(events)
            result["scraper_status"] = "ok"
        except Exception as exc:  # noqa: BLE001
            result["status"] = "degraded"
            result["scraper_status"] = f"error: {exc}"
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")
    return 0 if result["status"] == "ok" else 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args()
    if args.command == "scrape-scan":
        return run_scrape_scan(args)
    if args.command == "history-report":
        return run_history_report(args)
    if args.command == "health-check":
        return run_health_check(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
