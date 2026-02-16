from iddiaarb.notifier import build_alert_lines


def test_build_alert_lines_limits_count():
    payload = [
        {"event": "A vs B", "market": "1x2", "margin_pct": 1.1, "profit": 10.0},
        {"event": "C vs D", "market": "ml", "margin_pct": 1.2, "profit": 12.0},
    ]
    lines = build_alert_lines(payload, top_n=1)
    assert len(lines) == 1
    assert "A vs B" in lines[0]

