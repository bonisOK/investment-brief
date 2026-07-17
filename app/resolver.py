"""DR code -> real global ticker, via overrides, Yahoo symbol search, and a SQLite cache."""
import re
import time

import requests

from . import db
from .config import OVERRIDES, UA

YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_TRAILING_DIGITS = re.compile(r"\d+$")
# Yahoo exchange codes for primary US listings, preferred when several matches exist.
_US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "PCX", "ASE", "BTS"}


def strip_dr(code: str) -> str:
    return _TRAILING_DIGITS.sub("", code.strip().upper())


def _search(query: str) -> list[dict]:
    for attempt in (1, 2):
        r = requests.get(
            YAHOO_SEARCH,
            params={"q": query, "quotesCount": 10, "newsCount": 0},
            headers=UA,
            timeout=20,
        )
        if r.status_code == 429 and attempt == 1:
            time.sleep(3)
            continue
        r.raise_for_status()
        return [q for q in r.json().get("quotes", []) if q.get("symbol")]
    return []


def _best_equity(quotes: list[dict], prefer_symbol: str | None = None) -> dict | None:
    equities = [q for q in quotes if q.get("quoteType") == "EQUITY"] or quotes
    if not equities:
        return None
    if prefer_symbol:
        for q in equities:
            if q["symbol"].upper() == prefer_symbol.upper():
                return q
    us = [q for q in equities if q.get("exchange") in _US_EXCHANGES]
    return (us or equities)[0]


def _name_of(q: dict) -> str:
    return q.get("longname") or q.get("shortname") or q["symbol"]


def resolve(dr_code: str, thai: bool = False) -> dict:
    """Return {dr_code, real_ticker, company_name, exchange}; cached after first lookup."""
    dr_code = dr_code.strip().upper()
    cached = db.get_cached_ticker(dr_code)
    if cached:
        return {"dr_code": dr_code, "real_ticker": cached["real_ticker"],
                "company_name": cached["company_name"], "exchange": cached["exchange"]}

    base = strip_dr(dr_code)
    override = OVERRIDES.get(dr_code) or OVERRIDES.get(base)

    if override:
        symbol = override
    elif thai:
        symbol = f"{base}.BK"
    else:
        symbol = None

    quote = None
    try:
        if symbol:
            quote = _best_equity(_search(symbol), prefer_symbol=symbol)
            if quote and quote["symbol"].upper() != symbol.upper():
                quote = None  # search drifted; trust the explicit symbol
        else:
            quote = _best_equity(_search(base), prefer_symbol=base)
            if quote:
                symbol = quote["symbol"]
    except requests.RequestException:
        pass

    if not symbol:  # search failed entirely — last resort, use the base token
        symbol = f"{base}.BK" if thai else base

    name = _name_of(quote) if quote else base.title()
    exchange = (quote or {}).get("exchDisp") or (quote or {}).get("exchange") or ""

    db.cache_ticker(dr_code, symbol, name, exchange)
    return {"dr_code": dr_code, "real_ticker": symbol,
            "company_name": name, "exchange": exchange}
