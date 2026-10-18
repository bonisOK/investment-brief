"""Turn stored calendar events into the JSON the page's month grid renders.

Every money amount is pre-formatted here as "฿1,234" text, so the page's
Hide ฿ toggle masks it like any other amount.
"""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db
from .config import CFG

BKK = ZoneInfo("Asia/Bangkok")
KIND_BADGE = {"earnings": "📊", "xd": "💰", "pay": "💸"}
TAG_BADGE = {"earnings": "📊", "guidance": "🧭", "mna": "🤝", "legal": "⚖️",
             "leadership": "👤", "security": "🛡️", "policy": "🏛️", "other": "📰"}
SCOPE_ICON = {"us": "🌐", "set": "🇹🇭"}
DAY_ORDER = {"news": 0, "earnings": 1, "xd": 2, "pay": 3}
TONE_ORDER = {"bad": 0, "good": 1, "neutral": 2}


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


def _short(iso: str) -> str:
    return _d(iso).strftime("%-d %b")


def _money(dps, cash) -> str | None:
    if dps is None:
        return None
    s = f"{dps:.2f} THB/share"
    return s + (f" · est. ฿{cash:,.0f} before 10% tax" if cash else "")


def _lines(e: dict, today: date) -> list[str]:
    k = e["kind"]
    if k == "earnings":
        est, act = e.get("eps_est"), e.get("eps_actual")
        if act is not None:
            out = [f"{(e.get('result') or 'reported').capitalize()}: EPS {act:.2f}"
                   + (f" vs {est:.2f} est." if est is not None else "")
                   + (f" ({e['surprise_pct']:+.1f}%)" if e.get("surprise_pct") is not None else "")]
            if e.get("move_pct") is not None:
                when = "on the day" if e.get("timing") in ("before open", "during market") else "next day"
                out.append(f"Stock {e['move_pct']:+.1f}% {when}")
            return out
        out = [f"Reports {e['timing']}" if e.get("timing") else "Report time not set"]
        if _d(e["date"]) < today:
            out = ["Results not in yet"]
        if est is not None:
            out.append(f"EPS estimate {est:.2f}")
        return out
    if k == "xd":
        return ["Own it by the day before to get this dividend",
                _money(e.get("dps"), e.get("est_cash")) or "Amount not announced yet",
                f"Paid {_short(e['pay_date'])}" if e.get("pay_date") else "Pay date n/a"]
    if k == "pay":
        out = [_money(e.get("dps"), e.get("est_cash")) or "Amount n/a"]
        if e.get("xd_date"):
            out.append(f"XD was {_short(e['xd_date'])}")
        return out
    return []


def _title(e: dict) -> str:
    code = e.get("code") or ""
    return {"earnings": f"{code} earnings", "xd": f"{code} XD (ex-dividend)",
            "pay": f"{code} dividend paid"}.get(e["kind"]) or e.get("headline") or e.get("title") or ""


def _badge(e: dict) -> str:
    if e["kind"] != "news":
        return KIND_BADGE[e["kind"]]
    if e.get("tag") == "move":
        return "📉" if e.get("tone") == "bad" else "📈"
    return TAG_BADGE.get(e.get("tag"), "📰")


def _item(e: dict, domains: dict, today: date) -> dict:
    codes = e.get("codes") or ([e["code"]] if e.get("code") else [])
    return {
        "kind": e["kind"], "badge": _badge(e), "tone": e.get("tone") or "neutral",
        "codes": codes, "code": codes[0] if codes else None,
        "domain": domains.get(codes[0]) if codes else None,
        "icon": SCOPE_ICON.get(e.get("scope")) if not codes else None,
        "title": _title(e), "link": e.get("link"), "publisher": e.get("publisher"),
        "why": e.get("why"), "action": e.get("action"), "lines": _lines(e, today),
    }


def _fold_earnings_news(events: list[dict]) -> list[dict]:
    """Earnings news within a day of the stock's report joins that report's
    entry (headline link + takeaway) instead of being a second logo."""
    reports = {(e["code"], e["date"]): e for e in events if e["kind"] == "earnings"}
    out = []
    for e in events:
        if e["kind"] == "news" and e.get("tag") == "earnings":
            host = next((reports.get((c, (_d(e["date"]) + timedelta(days=dd)).isoformat()))
                         for c in e.get("codes") or [] for dd in (0, -1, 1)
                         if reports.get((c, (_d(e["date"]) + timedelta(days=dd)).isoformat()))), None)
            if host:
                host.update({k: e.get(k) for k in ("link", "publisher", "why", "action")})
                continue
        out.append(e)
    return out


def build(events: list[dict], domains: dict, today: date) -> dict:
    events = _fold_earnings_news([dict(e) for e in events])
    days: dict[str, list[dict]] = {}
    for e in events:
        days.setdefault(e["date"], []).append(_item(e, domains, today))
    for items in days.values():
        items.sort(key=lambda i: (DAY_ORDER[i["kind"]], TONE_ORDER.get(i["tone"], 2)))
    months = [d[:7] for d in days] + [today.isoformat()[:7]]
    return {"today": today.isoformat(), "first": min(months), "last": max(months), "days": days}


def cal_json(domains: dict, today: date | None = None) -> str:
    """Calendar JSON for the template, read from the events table."""
    today = today or datetime.now(BKK).date()
    c = CFG.get("calendar", {})
    events = db.events_between((today - timedelta(days=c.get("past_days", 120))).isoformat(),
                               (today + timedelta(days=c.get("future_days", 150))).isoformat())
    return json.dumps(build(events, domains, today), ensure_ascii=False)
