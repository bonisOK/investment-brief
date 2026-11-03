"""Daily ฿ price history for every ticker in the trade logs, and the
benchmark indexes. Pure helpers (align, fit_ratio) are separate from the
yfinance glue so they can be tested without the network."""
import logging
from bisect import bisect_right
from datetime import date, timedelta
from statistics import median

import yfinance as yf

from . import ledger

log = logging.getLogger(__name__)

INDEXES = {
    # key: (Yahoo symbol, multiply by USD/THB?)  SPY / QQQ dividend-adjusted.
    "spx": ("SPY", True),
    "ndx": ("QQQ", True),
    "set": ("^SET.BK", False),   # price only: Yahoo has no SET total-return
}


# ---------- pure helpers ----------

def align(series: dict[date, float], days: list[date]) -> list[float | None]:
    """Values on `days`, carrying the last known close forward (holidays
    differ between Bangkok and New York). None before the first close."""
    if not series:
        return [None] * len(days)
    keys = sorted(series)
    out = []
    for d in days:
        i = bisect_right(keys, d)
        out.append(series[keys[i - 1]] if i else None)
    return out


def value_on(series: dict[date, float], d: date) -> float | None:
    keys = sorted(series)
    i = bisect_right(keys, d)
    return series[keys[i - 1]] if i else None


def fit_ratio(points: list[tuple[date, float]], base: dict[date, float]) -> float | None:
    """DR price ÷ underlying ฿ price, fitted as the median over the user's own
    trade prices. The median shrugs off a mistyped row."""
    ratios = []
    for d, px in points:
        b = value_on(base, d)
        if px and b:
            ratios.append(px / b)
    return median(ratios) if ratios else None


def multiply(a: dict[date, float], b: dict[date, float]) -> dict[date, float]:
    """a x b on a's dates, using b's last value on or before each date."""
    out = {}
    for d, v in a.items():
        f = value_on(b, d)
        if f is not None:
            out[d] = v * f
    return out


def step_series(points: list[tuple[date, float]]) -> dict[date, float]:
    """Fallback: the trade prices themselves, held flat between trades."""
    return {d: p for d, p in sorted(points)}


# ---------- network glue ----------

class Fetcher:
    """Caches every Yahoo download for one run (both books share tickers)."""

    def __init__(self, start: date):
        self.start = start - timedelta(days=10)
        self._cache: dict[str, tuple[dict[date, float], str | None]] = {}

    def history(self, symbol: str) -> tuple[dict[date, float], str | None]:
        if symbol in self._cache:
            return self._cache[symbol]
        series, ccy = {}, None
        try:
            t = yf.Ticker(symbol)
            h = t.history(start=self.start.isoformat(), auto_adjust=True)
            if h is not None and len(h):
                closes = h["Close"].dropna()
                series = {ts.date(): float(v) for ts, v in closes.items()}
                ccy = (getattr(t, "history_metadata", None) or {}).get("currency")
        except Exception as e:
            log.info("no history for %s: %s", symbol, e)
        self._cache[symbol] = (series, ccy)
        return series, ccy

    def fx_to_thb(self, ccy: str | None) -> dict[date, float] | None:
        """Currency -> THB rate series; None when the price is already ฿."""
        if not ccy:
            return None
        c = ccy.upper()[:3]
        if c == "THB":
            return None
        series, _ = self.history("THB=X" if c == "USD" else f"{c}THB=X")
        return series or None

    def thb_history(self, symbol: str) -> dict[date, float]:
        series, ccy = self.history(symbol)
        fx = self.fx_to_thb(ccy)
        return multiply(series, fx) if (series and fx) else series

    def index(self, key: str) -> dict[date, float]:
        symbol, in_usd = INDEXES[key]
        series, _ = self.history(symbol)
        if in_usd and series:
            usd, _ = self.history("THB=X")
            series = multiply(series, usd) if usd else {}
        return series

    def ticker(self, code: str, underlying: str | None,
               points: list[tuple[date, float]]) -> tuple[dict[date, float], str]:
        """฿ price history for one DR / Thai code and where it came from:
        'dr' (its own SET listing), 'underlying' (fitted), or 'est' (trades)."""
        if ledger.is_dr(code):
            own, _ = self.history(f"{code}.BK")
            if len(own) >= 5:
                return own, "dr"
        for sym in [s for s in (underlying, None if ledger.is_dr(code) else f"{code}.BK") if s]:
            base = self.thb_history(sym)
            if len(base) >= 5:
                ratio = fit_ratio(points, base)
                if ratio:
                    return {d: v * ratio for d, v in base.items()}, "underlying"
        return step_series(points), "est"
