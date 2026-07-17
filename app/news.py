"""Google News RSS, queried by company name (there is no ticker feed).

Freshness is enforced in three ways: the `when:` operator asks Google for
recent items only, every entry's published date is parsed and hard-filtered
against the lookback window, and results are sorted newest-first before the
top N is kept (Google's own order is relevance, which surfaces stale stories).
Thai stocks are queried twice — by SET symbol ("HMPRO หุ้น") and by company
name — because Thai financial media usually writes the ticker.
"""
import calendar
import logging
import time
from urllib.parse import quote

import feedparser
import requests

from .config import CFG, UA

log = logging.getLogger(__name__)
RSS = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def _fetch_feed(query: str, hl: str, gl: str, ceid: str) -> list[dict]:
    url = RSS.format(q=quote(query), hl=hl, gl=gl, ceid=ceid)
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        feed = feedparser.parse(r.text)
    except Exception as e:
        log.warning("news fetch failed for %r: %s", query, e)
        return []
    now = time.time()
    items = []
    for e in feed.entries:
        ts = None
        if e.get("published_parsed"):
            ts = calendar.timegm(e.published_parsed)
        items.append(
            {
                "title": e.get("title", ""),
                "link": e.get("link", ""),
                "publisher": (e.get("source") or {}).get("title", ""),
                "published": e.get("published", ""),
                "age_h": round((now - ts) / 3600, 1) if ts else None,
            }
        )
    return items


def _fresh_sorted(items: list[dict], max_age_h: float) -> list[dict]:
    """Drop undated or too-old items, newest first."""
    dated = [i for i in items if i["age_h"] is not None and i["age_h"] <= max_age_h]
    return sorted(dated, key=lambda i: i["age_h"])


def age_label(age_h) -> str:
    if age_h is None:
        return "undated"
    return f"{age_h:.0f}h ago" if age_h < 24 else f"{age_h / 24:.0f}d ago"


def fetch_macro_news() -> list[dict]:
    """Market-wide context for the daily brief: US market + Thai SET feeds."""
    items = _fetch_feed("stock market today when:1d", "en-US", "US", "US:en")
    items += _fetch_feed("ตลาดหุ้นไทย SET วันนี้ when:1d", "th", "TH", "TH:th")
    return _dedupe(_fresh_sorted(items, 36))[:12]


def fetch_news(company_name: str, is_thai: bool = False, symbol: str | None = None) -> list[dict]:
    n = CFG["news"]["items_per_ticker"]
    lookback = CFG["news"]["lookback_days"]
    # Query the clean company name (drop legal suffixes that dilute results).
    q = company_name
    for suffix in (", Inc.", " Inc.", " Corporation", " Corp.", " PLC", " N.V.",
                   " S.A.", " Ltd.", " Limited", " Public Company", " ADR"):
        q = q.replace(suffix, "")
    q = q.strip().rstrip(",")
    when = f" when:{lookback}d"
    if is_thai:
        # Thai media writes the SET symbol, not the English company name.
        items = []
        if symbol:
            items += _fetch_feed(f'"{symbol}" หุ้น{when}', "th", "TH", "TH:th")
        items += _fetch_feed(q + when, "th", "TH", "TH:th")
    else:
        items = _fetch_feed(q + when, "en-US", "US", "US:en")
    return _dedupe(_fresh_sorted(items, lookback * 24))[:n]


def _dedupe(items: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for it in items:
        key = it["title"].lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(it)
    return unique
