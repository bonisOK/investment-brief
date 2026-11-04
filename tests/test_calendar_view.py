from datetime import date

from app import calendar_view as cv

TODAY = date(2026, 10, 18)
DOMAINS = {"MU80": "micron.com", "ADVANC": "ais.th"}


def test_days_months_and_badges():
    out = cv.build([
        {"id": "e1", "date": "2026-10-07", "kind": "earnings", "code": "MU80", "tone": "good",
         "timing": "after close", "eps_est": 2.0, "eps_actual": 2.3, "surprise_pct": 15.0,
         "result": "beat", "move_pct": 8.0},
        {"id": "n1", "date": "2026-10-17", "kind": "news", "code": None, "codes": [], "scope": "set",
         "tag": "move", "tone": "bad", "headline": "SET slides 3%", "link": "L", "publisher": "P",
         "why": "w", "action": ""},
        {"id": "x1", "date": "2026-12-02", "kind": "xd", "code": "ADVANC", "tone": "neutral",
         "dps": 4.5, "est_cash": 4500.0, "pay_date": None},
    ], DOMAINS, TODAY)
    assert (out["today"], out["first"], out["last"]) == ("2026-10-18", "2026-10", "2026-12")
    (earn,) = out["days"]["2026-10-07"]
    assert (earn["badge"], earn["domain"], earn["tone"]) == ("📊", "micron.com", "good")
    assert "Beat: EPS 2.30 vs 2.00 est. (+15.0%)" in earn["lines"]
    assert "Stock +8.0% next day" in earn["lines"]
    (mkt,) = out["days"]["2026-10-17"]
    assert (mkt["badge"], mkt["icon"], mkt["domain"]) == ("📉", "🇹🇭", None)
    (xd,) = out["days"]["2026-12-02"]
    assert xd["badge"] == "💰"
    assert "4.50 THB/share · est. ฿4,500 before 10% tax" in xd["lines"]
    assert "Pay date n/a" in xd["lines"]


def test_earnings_news_folds_into_the_earnings_event():
    out = cv.build([
        {"id": "e1", "date": "2026-10-07", "kind": "earnings", "code": "MU80", "tone": "good",
         "timing": "after close", "result": "beat", "eps_est": 2.0, "eps_actual": 2.3},
        {"id": "n1", "date": "2026-10-08", "kind": "news", "code": "MU80", "codes": ["MU80"],
         "scope": "stock", "tag": "earnings", "tone": "good", "headline": "Beat lifts outlook",
         "link": "L", "publisher": "P", "why": "w", "action": "Good for MU80"},
    ], DOMAINS, TODAY)
    assert "2026-10-08" not in out["days"]
    (earn,) = out["days"]["2026-10-07"]
    assert (earn["link"], earn["action"]) == ("L", "Good for MU80")


def test_upcoming_earnings_lines_and_day_order():
    out = cv.build([
        {"id": "p", "date": "2026-11-05", "kind": "pay", "code": "ADVANC", "tone": "neutral",
         "dps": 4.5, "est_cash": None, "xd_date": "2026-10-20"},
        {"id": "e", "date": "2026-11-05", "kind": "earnings", "code": "MU80", "tone": "neutral",
         "timing": None, "eps_est": 2.5},
    ], DOMAINS, TODAY)
    first, second = out["days"]["2026-11-05"]
    assert first["kind"] == "earnings" and second["kind"] == "pay"
    assert first["lines"] == ["Report time not set", "EPS estimate 2.50"]
    assert second["lines"] == ["4.50 THB/share", "XD was 20 Oct"]


def test_earn_flag_marks_reports_and_unfolded_earnings_news():
    out = cv.build([
        {"id": "e1", "date": "2026-10-07", "kind": "earnings", "code": "MU80", "tone": "good"},
        {"id": "n1", "date": "2026-10-20", "kind": "news", "code": "ADVANC", "codes": ["ADVANC"],
         "scope": "stock", "tag": "earnings", "tone": "bad", "headline": "Profit warning"},
        {"id": "n2", "date": "2026-10-20", "kind": "news", "code": "ADVANC", "codes": ["ADVANC"],
         "scope": "stock", "tag": "legal", "tone": "bad", "headline": "Lawsuit"},
        {"id": "x1", "date": "2026-12-02", "kind": "xd", "code": "ADVANC", "tone": "neutral"},
    ], DOMAINS, TODAY)
    assert out["days"]["2026-10-07"][0]["earn"] is True
    assert {i["title"]: i["earn"] for i in out["days"]["2026-10-20"]} == {
        "Profit warning": True, "Lawsuit": False}
    assert out["days"]["2026-12-02"][0]["earn"] is False
