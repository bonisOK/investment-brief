"""Daily ฿ price history for every ticker in the trade logs, and the
benchmark indexes. Pure helpers (align, fit_ratio) are separate from the
yfinance glue so they can be tested without the network."""
import logging
from bisect import bisect_right
from datetime import date, datetime, time, timedelta
from statistics import median

import requests
import yfinance as yf

from . import ledger
from .config import UA

log = logging.getLogger(__name__)

INDEXES = {
    # key: (Yahoo symbol, multiply by USD/THB?)  SPY / QQQ dividend-adjusted.
    "spx": ("SPY", True),
    "ndx": ("QQQ", True),
    "set": ("^SET.BK", False),   # price only: Yahoo has no SET total-return
}
MIN_POINTS = 20
# Yahoo's SET index history comes back empty from time to time, so SET has
# fallbacks, tried in order. The label tells the page which one it got.
SET_SOURCES = [
    ("yf", "^SET.BK", "SET index, price only"),
    ("chart", "^SET.BK", "SET index, price only"),
    ("setapi", "SET", "SET index, price only (set.or.th)"),
    ("yf", "TDEX.BK", "SET50 ETF TDEX (fallback)"),
    ("yf", "^SET50.BK", "SET50 index (fallback)"),
]
SET_API = "https://www.set.or.th/api/set/index/{sym}/chart-quotation?period=5Y&accumulated=false"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"


def _parse_quotes(payload) -> dict[date, float]:
    """set.or.th chart-quotation JSON -> {date: close}. Read defensively:
    the list may sit under 'quotations' or be the payload itself, and the
    value under 'price', 'close', 'value' or 'index'."""
    rows = payload.get("quotations") if isinstance(payload, dict) else payload
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        raw = r.get("datetime") or r.get("date") or r.get("tradingDate")
        v = next((r[k] for k in ("price", "close", "value", "index") if isinstance(r.get(k), (int, float))), None)
        if not raw or v is None:
            continue
        try:
            out[date.fromisoformat(str(raw)[:10])] = float(v)
        except ValueError:
            continue
    return out


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

    def yahoo_chart(self, symbol: str) -> dict[date, float]:
        """Yahoo's chart API called directly: works some days when the
        yfinance wrapper comes back empty."""
        try:
            r = requests.get(YAHOO_CHART.format(sym=symbol), headers=UA, timeout=20,
                             params={"period1": int(datetime.combine(self.start, time()).timestamp()),
                                     "period2": int(datetime.now().timestamp()), "interval": "1d"})
            res = r.json()["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            return {datetime.utcfromtimestamp(t).date(): float(c)
                    for t, c in zip(res["timestamp"], closes) if c is not None}
        except Exception as e:
            log.info("yahoo chart failed for %s: %s", symbol, e)
            return {}

    def set_api(self, symbol: str) -> dict[date, float]:
        try:
            r = requests.get(SET_API.format(sym=symbol), timeout=20,
                             headers={**UA, "Referer": "https://www.set.or.th/", "Accept": "application/json"})
            return {d: v for d, v in _parse_quotes(r.json()).items() if d >= self.start}
        except Exception as e:
            log.info("set.or.th failed for %s: %s", symbol, e)
            return {}

    def index(self, key: str) -> tuple[dict[date, float], str | None]:
        """(฿ series, label of the source used). Empty series when all fail."""
        symbol, in_usd = INDEXES[key]
        if key == "set":
            for kind, sym, label in SET_SOURCES:
                series = (self.history(sym)[0] if kind == "yf" else
                          self.yahoo_chart(sym) if kind == "chart" else self.set_api(sym))
                log.info("index set via %s %s: %d points", kind, sym, len(series))
                if len(series) >= MIN_POINTS:
                    return series, label
            return {}, None
        series, _ = self.history(symbol)
        if in_usd and series:
            usd, _ = self.history("THB=X")
            series = multiply(series, usd) if usd else {}
        log.info("index %s via %s: %d points", key, symbol, len(series))
        return series, None

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
