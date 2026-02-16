from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BacktestReport:
    total_records: int
    runs: int
    unique_fingerprints: int
    avg_margin_pct: float
    avg_profit: float
    reappeared_next_run_ratio: float


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                command TEXT NOT NULL,
                meta_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_label TEXT NOT NULL,
                market_key TEXT NOT NULL,
                margin_pct REAL NOT NULL,
                profit REAL NOT NULL,
                expected_profit REAL NOT NULL,
                risk_score REAL NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_opp_run_id ON opportunities(run_id);
            CREATE INDEX IF NOT EXISTS idx_opp_fingerprint ON opportunities(fingerprint);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _fingerprint(opp: dict) -> str:
    return "|".join(
        [
            str(opp.get("event_id", "")),
            str(opp.get("market", "")),
            json.dumps(opp.get("bookmakers", {}), sort_keys=True),
        ]
    )


def save_run(db_path: str, command: str, meta: dict, opportunities: list[dict]) -> int:
    init_db(db_path)
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO runs(created_at, command, meta_json) VALUES (?, ?, ?)",
            (now, command, json.dumps(meta, ensure_ascii=False)),
        )
        run_id = int(cur.lastrowid)
        for opp in opportunities:
            cur.execute(
                """
                INSERT INTO opportunities(
                    run_id, fingerprint, event_id, event_label, market_key,
                    margin_pct, profit, expected_profit, risk_score, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    _fingerprint(opp),
                    str(opp.get("event_id", "")),
                    str(opp.get("event", "")),
                    str(opp.get("market", "")),
                    float(opp.get("margin_pct", 0.0)),
                    float(opp.get("profit", 0.0)),
                    float(opp.get("expected_profit", 0.0)),
                    float(opp.get("risk_score", 0.0)),
                    json.dumps(opp, ensure_ascii=False),
                ),
            )
        conn.commit()
        return run_id
    finally:
        conn.close()


def backtest_report(db_path: str, last_runs: int = 100) -> BacktestReport:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        run_rows = cur.execute(
            "SELECT id FROM runs ORDER BY id DESC LIMIT ?",
            (max(1, last_runs),),
        ).fetchall()
        run_ids = [int(r[0]) for r in run_rows]
        if not run_ids:
            return BacktestReport(0, 0, 0, 0.0, 0.0, 0.0)
        qmarks = ",".join("?" for _ in run_ids)
        rows = cur.execute(
            f"""
            SELECT run_id, fingerprint, margin_pct, profit
            FROM opportunities
            WHERE run_id IN ({qmarks})
            ORDER BY run_id ASC
            """,
            tuple(run_ids),
        ).fetchall()
        if not rows:
            return BacktestReport(0, len(run_ids), 0, 0.0, 0.0, 0.0)

        total = len(rows)
        unique = len({str(r[1]) for r in rows})
        avg_margin = sum(float(r[2]) for r in rows) / total
        avg_profit = sum(float(r[3]) for r in rows) / total

        by_run: dict[int, set[str]] = {}
        for run_id, fp, *_ in rows:
            by_run.setdefault(int(run_id), set()).add(str(fp))
        sorted_runs = sorted(by_run.keys())
        reappeared = 0
        compared = 0
        for i in range(len(sorted_runs) - 1):
            now_set = by_run[sorted_runs[i]]
            next_set = by_run[sorted_runs[i + 1]]
            compared += len(now_set)
            reappeared += len(now_set & next_set)
        ratio = (reappeared / compared) if compared > 0 else 0.0

        return BacktestReport(
            total_records=total,
            runs=len(run_ids),
            unique_fingerprints=unique,
            avg_margin_pct=avg_margin,
            avg_profit=avg_profit,
            reappeared_next_run_ratio=ratio,
        )
    finally:
        conn.close()
