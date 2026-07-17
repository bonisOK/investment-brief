"""Read the two Google Sheet tabs via the gviz CSV endpoint."""
import io
import re
from urllib.parse import quote

import pandas as pd
import requests

from .config import SHEET_ID, UA

GVIZ = "https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={tab}"


def _fetch_tab(tab: str, sheet_id: str = SHEET_ID) -> pd.DataFrame:
    url = GVIZ.format(sid=sheet_id, tab=quote(tab))
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    # Headers carry trailing spaces and embedded newlines ("Annual\nDividend ").
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    return df


def _num(x):
    """'252,127.25' / '11.57%' / '' -> float or None."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _text(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    return "" if s.lower() == "nan" else s


def read_growth(sheet_id: str = SHEET_ID) -> list[dict]:
    """Tab A: MINT MEGA FUNDs. One row per DR ticker; Section is forward-filled.

    Buy/sell drift compares the sheet's Target Cost against the live Current
    Value column (market value), not against the amount originally bought.
    """
    df = _fetch_tab("MINT MEGA FUNDs", sheet_id)
    rows, section = [], ""
    for _, r in df.iterrows():
        sec = _text(r.get("Section"))
        if sec:
            section = sec
        if section.upper() in ("CASH", "MINDSET"):
            continue
        ticker = _text(r.get("Ticker"))
        if not ticker:
            continue
        target = _num(r.get("Target Cost")) or 0.0
        value = _num(r.get("Current Value")) or 0.0
        rows.append(
            {
                "book": "growth",
                "section": section,
                "dr_code": ticker.upper(),
                "reasons": _text(r.get("Reasons")),
                "buy_sell_thb": round(target - value, 2),
                "have_to_buy": _num(r.get("Real Have to buy")),
                "target_cost": target,
                "current_value": value,
                "average_cost": _num(r.get("Average Cost")),
                "current_shares": _num(r.get("Current Shares")),
                "unrealized_pct": _num(r.get("Unrealized %")),
                "unrealized_gain": _num(r.get("Unrealized Gain")),
                "is_thai": section.upper() == "THAI",
            }
        )
    return rows


def read_dividend(sheet_id: str = SHEET_ID) -> list[dict]:
    """Tab B: Dividends. Same shape as the growth tab, with sections
    THAI / US / ASIA / EUROPE. Drift uses the sheet's own 'Real Have to buy'."""
    df = _fetch_tab("Dividends", sheet_id)
    rows, section = [], ""
    for _, r in df.iterrows():
        sec = _text(r.get("Section"))
        if sec:
            section = sec
        ticker = _text(r.get("Ticker"))
        if not ticker:
            continue
        have_to_buy = _num(r.get("Real Have to buy"))
        rows.append(
            {
                "book": "dividend",
                "section": section,
                "dr_code": ticker.upper(),
                "reasons": _text(r.get("Reasons")),
                "buy_sell_thb": have_to_buy,
                "have_to_buy": have_to_buy,
                "target_cost": _num(r.get("Target Cost")),
                "bought": _num(r.get("Bought")),
                "average_cost": _num(r.get("Average Cost")),
                "current_shares": _num(r.get("Current Shares")),
                "sheet_yield_pct": _num(r.get("Dividend")),
                "annual_dividend": _num(r.get("Annual Dividend")),
                "is_thai": section.upper() == "THAI",
            }
        )
    return rows
