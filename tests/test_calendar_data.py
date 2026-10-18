from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app import calendar_data as cd

NY, BKK = ZoneInfo("America/New_York"), ZoneInfo("Asia/Bangkok")
TODAY = date(2026, 10, 18)


def closes(tz, values_by_day):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz=tz) for d in values_by_day])
    return pd.Series(list(values_by_day.values()), index=idx)


def earnings_df(rows):
    idx = pd.DatetimeIndex([pd.Timestamp(ts, tz=NY) for ts, *_ in rows], name="Earnings Date")
    return pd.DataFrame({"EPS Estimate": [r[1] for r in rows],
                         "Reported EPS": [r[2] for r in rows],
                         "Surprise(%)": [r[3] for r in rows],
                         "Event Type": [r[4] for r in rows]}, index=idx)


def test_earnings_after_close_beat_with_next_day_move():
    df = earnings_df([("2026-10-07 16:00", 2.00, 2.30, 15.0, "Earnings"),
                      ("2026-11-05 07:00", 2.50, np.nan, np.nan, "Earnings"),
                      ("2026-10-07 17:00", np.nan, np.nan, np.nan, "Call")])
    px = closes(NY, {"2026-10-06": 90, "2026-10-07": 100, "2026-10-08": 108})
    evs = {e["id"]: e for e in cd.earnings_events("APPL03", df, px, TODAY)}

    past = evs["earn:APPL03:2026-10-07"]
    assert (past["timing"], past["result"], past["tone"]) == ("after close", "beat", "good")
    assert past["eps_actual"] == 2.30 and past["surprise_pct"] == 15.0
    assert past["move_pct"] == 8.0
    up = evs["earn:APPL03:2026-11-05"]
    assert (up["timing"], up["result"], up["tone"], up["move_pct"]) == ("before open", None, "neutral", None)
    assert up["eps_est"] == 2.5
    assert len(evs) == 2  # the earnings call row is not a second report


def test_earnings_before_open_miss_uses_same_day_move():
    df = earnings_df([("2026-10-14 06:30", 1.00, 0.80, -20.0, "Earnings")])
    px = closes(NY, {"2026-10-13": 50, "2026-10-14": 45})
    (e,) = cd.earnings_events("MU", df, px, TODAY)
    assert (e["result"], e["tone"], e["move_pct"]) == ("miss", "bad", -10.0)


def test_earnings_outside_window_and_empty_frames_are_ignored():
    df = earnings_df([("2025-01-10 16:00", 1, 1, 0, "Earnings")])
    assert cd.earnings_events("X", df, None, TODAY) == []
    assert cd.earnings_events("X", None, None, TODAY) == []


def test_dividends_past_xd_upcoming_xd_and_pay_date():
    divs = pd.Series([4.0], index=pd.DatetimeIndex([pd.Timestamp("2026-08-12", tz=BKK)]))
    cal = {"Ex-Dividend Date": date(2026, 10, 28), "Dividend Date": date(2026, 11, 14)}
    evs = {e["id"]: e for e in cd.dividend_events("ADVANC", divs, cal, shares=1000, today=TODAY)}

    past = evs["xd:ADVANC:2026-08-12"]
    assert (past["dps"], past["est_cash"], past["pay_date"]) == (4.0, 4000.0, None)
    nxt = evs["xd:ADVANC:2026-10-28"]
    assert (nxt["dps"], nxt["pay_date"]) == (None, "2026-11-14")
    pay = evs["pay:ADVANC:2026-11-14"]
    assert (pay["kind"], pay["xd_date"]) == ("pay", "2026-10-28")


def test_dividends_calendar_pay_date_attaches_to_matching_past_xd():
    divs = pd.Series([1.5], index=pd.DatetimeIndex([pd.Timestamp("2026-10-10", tz=BKK)]))
    cal = {"Ex-Dividend Date": date(2026, 10, 10), "Dividend Date": date(2026, 10, 30)}
    evs = {e["id"]: e for e in cd.dividend_events("TISCO", divs, cal, shares=None, today=TODAY)}
    assert evs["xd:TISCO:2026-10-10"]["pay_date"] == "2026-10-30"
    assert evs["xd:TISCO:2026-10-10"]["est_cash"] is None
    assert evs["pay:TISCO:2026-10-30"]["dps"] == 1.5


def test_news_events_only_calendar_items_dated_by_publication():
    run_at = datetime(2026, 10, 18, 6, 0, tzinfo=BKK)
    items = [
        {"calendar": True, "tag": "legal", "scope": "stock", "tickers": ["BABA80"],
         "implication": "bearish", "age_h": 10, "headline": "Fine dents EU growth",
         "title": "Alibaba fined", "link": "L1", "publisher": "Bloomberg", "why": "w", "action": "Bad for BABA80"},
        {"calendar": True, "tag": "policy", "scope": "us", "tickers": [],
         "implication": "bearish", "age_h": 2, "headline": "Fed surprise hike",
         "title": "Fed hikes", "link": "L2", "publisher": "Reuters", "why": "w", "action": ""},
        {"calendar": False, "tag": "other", "scope": "stock", "tickers": ["MU"], "age_h": 1,
         "implication": "neutral", "headline": "h", "title": "t", "link": "L3", "publisher": "p"},
    ]
    evs = cd.news_events(items, run_at)
    assert len(evs) == 2
    stock, market = evs
    assert (stock["date"], stock["code"], stock["codes"], stock["tone"]) == ("2026-10-17", "BABA80", ["BABA80"], "bad")
    assert stock["id"] == "news:2026-10-17:BABA80:legal"
    assert (market["date"], market["code"], market["scope"]) == ("2026-10-18", None, "us")
    assert market["id"].startswith("news:2026-10-18:us:")


def test_title_key_ignores_publisher_suffix_and_punctuation():
    from app.news import title_key
    assert title_key("Alibaba Fined €550 Million! - Bloomberg.com") == title_key("alibaba fined €550 million")
