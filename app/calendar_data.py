"""Calendar events: earnings, Thai XD / pay dates, and very important news.

Each collector is a pure function over yfinance-shaped data (DataFrame /
Series / calendar dict), so it can be tested without the network. `collect`
is the thin wrapper that pulls that data for one holding.

Event shape (flat dict, stored as JSON in the `events` table):
    id, date (YYYY-MM-DD), kind (earnings | xd | pay | news), code (DR code
    or None for market news), tone (good | bad | neutral), plus kind fields.
"""
import hashlib
import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import CFG
from .news import title_key

log = logging.getLogger(__name__)
BKK = ZoneInfo("Asia/Bangkok")


def _window(today: date) -> tuple[date, date]:
    c = CFG.get("calendar", {})
    return (today - timedelta(days=c.get("past_days", 120)),
            today + timedelta(days=c.get("future_days", 150)))


def _num(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _timing(ts) -> str | None:
    """Report time in the exchange's own timezone -> session label."""
    minutes = ts.hour * 60 + ts.minute
    if minutes == 0:
        return None  # date-only entry, time unknown
    if minutes < 9 * 60 + 30:
        return "before open"
    if minutes >= 16 * 60:
        return "after close"
    return "during market"


def _reaction(closes, day: date, timing: str | None) -> float | None:
    """The stock's move on the report: next day for after-close (or unknown
    time) reports, same day for before-open / intraday ones."""
    if closes is None or not len(closes):
        return None
    by_day = {ts.date(): float(v) for ts, v in closes.dropna().items()}
    days = sorted(by_day)
    if timing in ("before open", "during market"):
        before = [d for d in days if d < day]
        after = [d for d in days if d >= day]
    else:
        before = [d for d in days if d <= day]
        after = [d for d in days if d > day]
    if not before or not after or not by_day[before[-1]]:
        return None
    return round((by_day[after[0]] / by_day[before[-1]] - 1) * 100, 1)


def earnings_events(code: str, df, closes, today: date) -> list[dict]:
    """From Ticker.get_earnings_dates(): one event per report in the window."""
    if df is None or getattr(df, "empty", True):
        return []
    start, end = _window(today)
    out = []
    for ts, r in df.iterrows():
        if "Event Type" in df.columns and r.get("Event Type") not in (None, "Earnings"):
            continue
        day = ts.date()
        if not start <= day <= end:
            continue
        est, actual = _num(r.get("EPS Estimate")), _num(r.get("Reported EPS"))
        surprise = _num(r.get("Surprise(%)"))
        result = None
        if actual is not None and est is not None:
            result = "beat" if actual > est else "miss" if actual < est else "in line"
            if surprise is None and est:
                surprise = round((actual - est) / abs(est) * 100, 1)
        timing = _timing(ts)
        reported = actual is not None and day <= today
        out.append({
            "id": f"earn:{code}:{day.isoformat()}", "date": day.isoformat(),
            "kind": "earnings", "code": code, "timing": timing,
            "eps_est": est, "eps_actual": actual, "surprise_pct": surprise,
            "result": result,
            "tone": {"beat": "good", "miss": "bad"}.get(result, "neutral"),
            "move_pct": _reaction(closes, day, timing) if reported else None,
        })
    return out


def dividend_events(code: str, dividends, cal: dict | None, shares: float | None,
                    today: date) -> list[dict]:
    """XD events from Ticker.dividends (past, with amount) and Ticker.calendar
    (next XD + pay date, amount usually not announced yet), plus pay events."""
    start, end = _window(today)
    cal = cal or {}
    cal_xd, cal_pay = cal.get("Ex-Dividend Date"), cal.get("Dividend Date")
    if not isinstance(cal_xd, date):
        cal_xd = None
    if not (isinstance(cal_pay, date) and cal_xd and cal_xd < cal_pay <= cal_xd + timedelta(days=90)):
        cal_pay = None  # Yahoo sometimes pairs the next XD with last year's pay date

    xds: dict[date, float | None] = {}
    if dividends is not None and len(dividends):
        for ts, amt in dividends.items():
            if start <= ts.date() <= end:
                xds[ts.date()] = _num(amt)
    if cal_xd and start <= cal_xd <= end:
        xds.setdefault(cal_xd, None)

    out = []
    for day, dps in sorted(xds.items()):
        cash = round(dps * shares, 0) if dps is not None and shares else None
        pay = cal_pay.isoformat() if cal_pay and day == cal_xd else None
        out.append({"id": f"xd:{code}:{day.isoformat()}", "date": day.isoformat(),
                    "kind": "xd", "code": code, "tone": "neutral",
                    "dps": dps, "est_cash": cash, "pay_date": pay})
        if pay:
            out.append({"id": f"pay:{code}:{pay}", "date": pay, "kind": "pay",
                        "code": code, "tone": "neutral", "xd_date": day.isoformat(),
                        "dps": dps, "est_cash": cash})
    return out


def news_events(items: list[dict], run_at: datetime) -> list[dict]:
    """Brief items the AI flagged as calendar-worthy, put on the day they
    broke. One story per stock + type + day, so a re-run doesn't duplicate."""
    out = []
    for it in items:
        if not it.get("calendar"):
            continue
        age = it.get("age_h")
        when = run_at - timedelta(hours=age) if isinstance(age, (int, float)) else run_at
        day = when.astimezone(BKK).date().isoformat()
        tag = it.get("tag") or "other"
        codes = sorted(it.get("tickers") or [])
        if codes:
            eid, scope = f"news:{day}:{'+'.join(codes)}:{tag}", "stock"
        else:
            scope = it.get("scope") if it.get("scope") in ("us", "set") else "us"
            digest = hashlib.sha1(title_key(it.get("title", "")).encode()).hexdigest()[:8]
            eid = f"news:{day}:{scope}:{digest}"
        out.append({
            "id": eid, "date": day, "kind": "news", "code": codes[0] if codes else None,
            "codes": codes, "scope": scope, "tag": tag,
            "tone": {"bullish": "good", "bearish": "bad"}.get(it.get("implication"), "neutral"),
            "headline": it.get("headline") or it.get("title"), "title": it.get("title"),
            "link": it.get("link"), "publisher": it.get("publisher"),
            "why": it.get("why"), "action": it.get("action"),
        })
    return out


def collect(row: dict, t, hist, today: date) -> tuple[list[dict], set[str]]:
    """Fetch earnings (+ Thai dividends) for one holding. Returns (events,
    kinds fetched for real) — only those kinds may prune stale future dates,
    so a Yahoo hiccup never wipes known upcoming events."""
    code = row["dr_code"]
    closes = hist["Close"] if hist is not None and "Close" in hist else None
    events, fresh = [], set()
    try:
        df = t.get_earnings_dates(limit=12)
        if df is not None and not df.empty:
            events += earnings_events(code, df, closes, today)
            fresh.add("earnings")
    except Exception as e:
        log.info("no earnings dates for %s: %s", code, str(e)[:120])
    if row.get("is_thai"):
        try:
            cal = t.calendar or {}
            events += dividend_events(code, t.dividends, cal, row.get("current_shares"), today)
            if cal.get("Ex-Dividend Date"):
                fresh.update({"xd", "pay"})
        except Exception as e:
            log.info("no dividend dates for %s: %s", code, str(e)[:120])
    return events, fresh
