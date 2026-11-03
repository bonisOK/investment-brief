"""Pipeline wiring with every network call faked: events and shown-news
land in the DB, and the saved snapshot stays JSON-clean."""
from app import ai, calendar_data, db, news, pipeline, prices, resolver, sentiment, sheets


def test_run_stores_events_marks_shown_and_dedupes_next_brief(monkeypatch):
    row = {"book": "growth", "section": "US", "dr_code": "MU80", "buy_sell_thb": 0.0,
           "current_value": 1000.0, "is_thai": False}
    monkeypatch.setattr(pipeline, "_refresh_sheet_prices", lambda: None)
    monkeypatch.setattr(sheets, "read_growth", lambda: [dict(row)])
    monkeypatch.setattr(sheets, "read_dividend", lambda: [])
    monkeypatch.setattr(sentiment, "fetch_fng", lambda: {"score": 50, "label": "Neutral"})
    monkeypatch.setattr(resolver, "resolve", lambda c, thai=False: {
        "dr_code": c, "real_ticker": "MU", "company_name": "Micron", "exchange": "NASDAQ"})
    monkeypatch.setattr(pipeline.yf, "Ticker", lambda s: object())
    monkeypatch.setattr(prices, "history", lambda t: None)
    monkeypatch.setattr(prices, "fetch_prices", lambda s, t=None, hist=None: {"price": 100.0, "chg_1d": -6.0})
    monkeypatch.setattr(pipeline, "valuation_safe", lambda s, px: {"undervalued_pct": None})
    monkeypatch.setattr(news, "fetch_news", lambda *a, **k: [
        {"title": "Micron cuts guidance - Reuters", "link": "L", "publisher": "Reuters", "age_h": 3}])
    monkeypatch.setattr(news, "fetch_macro_news", lambda: [])
    monkeypatch.setattr(calendar_data, "collect", lambda row, t, hist, today: (
        [{"id": "earn:MU80:2026-12-17", "date": "2026-12-17", "kind": "earnings",
          "code": "MU80", "tone": "neutral"}], {"earnings"}))
    monkeypatch.setattr(ai, "analyze_batch", lambda rows: {})
    monkeypatch.setattr(pipeline.portfolio, "build", lambda rows, today=None: {"growth": {"error": "x"}})
    monkeypatch.setattr(ai, "decide_amounts", lambda rows: {})
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(ai, "CALL_GAP_S", 0)
    calls = []
    monkeypatch.setattr(ai, "_generate", lambda p: calls.append(p) or (
        '{"items": [{"id": "H1", "tickers": ["MU80"], "headline": "Guidance cut bites",'
        ' "implication": "bearish", "why": "w", "action": "Bad for MU80", "urgency": "act",'
        ' "calendar": true, "tag": "guidance"}]}'))

    snap = pipeline.run_pipeline()

    assert "_events" not in snap["rows"][0] and "_fresh" not in snap["rows"][0]
    assert snap["brief"]["items"][0]["headline"] == "Guidance cut bites"
    kinds = {e["kind"] for e in db.events_between("2000-01-01", "2100-01-01")}
    assert kinds == {"earnings", "news"}
    assert news.title_key("Micron cuts guidance") in db.shown_keys()

    # Second run, same headline: nothing new, so no Gemini call and no items.
    snap2 = pipeline.run_pipeline()
    assert len(calls) == 1
    assert snap2["brief"]["items"] == []


def test_dividend_cash_uses_shares_from_both_books():
    results = [{"dr_code": "ADVANC", "is_thai": True, "current_shares": 1000},
               {"dr_code": "ADVANC", "is_thai": True, "current_shares": 500},
               {"dr_code": "MU80", "is_thai": False, "current_shares": 9}]
    ev = {"id": "xd:ADVANC:2026-10-28", "date": "2026-10-28", "kind": "xd", "code": "ADVANC",
          "dps": 4.0, "est_cash": 4000.0}
    out = pipeline._combine_events([dict(ev), dict(ev, est_cash=2000.0)], results)
    assert len(out) == 1 and out[0]["est_cash"] == 6000.0
