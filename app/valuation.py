"""Intrinsic value: FMP DCF (primary, US/global) + analyst target fallback;
Thai stocks use dividend yield vs 5-year average as a value proxy."""
import logging

import requests

from .config import FMP_API_KEY

log = logging.getLogger(__name__)
# The v3 DCF endpoint is legacy-only (pre-Aug-2025 subscriptions); new keys
# must use the /stable/ API.
FMP_DCF = "https://financialmodelingprep.com/stable/discounted-cash-flow"


def _fmp_dcf(ticker: str) -> float | None:
    if not FMP_API_KEY:
        return None
    try:
        r = requests.get(FMP_DCF,
                         params={"symbol": ticker, "apikey": FMP_API_KEY},
                         timeout=20)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return float(data[0].get("dcf") or 0) or None
    except Exception as e:
        log.warning("FMP DCF failed for %s: %s", ticker, e)
    return None


def fetch_valuation(ticker: str, prices: dict) -> dict:
    """Return {dcf_value, undervalued_pct, value_basis}.
    undervalued_pct > 0 means the stock looks cheap."""
    price = prices.get("price")
    is_thai = ticker.upper().endswith(".BK")
    gaps: list[float] = []
    basis: list[str] = []

    dcf = None if is_thai else _fmp_dcf(ticker)
    if dcf and price:
        gaps.append((dcf - price) / price * 100)
        basis.append("DCF")

    target = prices.get("target_price")
    if target and price:
        gaps.append((target - price) / price * 100)
        basis.append("analyst target")

    # Thai value proxy: current dividend yield vs its own 5-year average.
    if is_thai:
        dy, avg = prices.get("dividend_yield"), prices.get("five_year_avg_yield")
        if dy and avg:
            gaps.append(max(min((dy - avg) / avg * 100, 50), -50))
            basis.append("yield vs 5y avg")

    undervalued = round(sum(gaps) / len(gaps), 1) if gaps else None
    return {
        "dcf_value": round(dcf, 2) if dcf else None,
        "undervalued_pct": undervalued,
        "value_basis": " + ".join(basis) if basis else "no data",
    }
