from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "iddiaarb.cli",
        "scrape-scan",
        "--scraper-source",
        "flashscore",
        "--geo-ip-code",
        "GB",
        "--geo-ip-subdivision",
        "GBENG",
        "--max-events",
        "5",
        "--format",
        "json",
        "--disable-history",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip())
        return proc.returncode
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("invalid_json_output")
        return 2
    meta = payload.get("meta", {})
    events = meta.get("events_with_h2h", 0) or meta.get("events_with_quorum", 0)
    print(json.dumps({"count": payload.get("count", 0), "events": events, "meta_source": meta.get("source")}))
    if int(events) <= 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
