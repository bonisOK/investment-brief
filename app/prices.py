"""yfinance: price, 1D/1W/1M momentum, PE, analyst target, dividend yield,
and a CNN-style per-stock fear/greed composite (0 = extreme fear, 100 = greed)."""
import logging

import yfinance as yf

from .config import CFG

log = logging.getLogger(__name__)


def _scale(x: float, sat: float) -> float:
    """Map a signed signal to 0-100, saturating at ±sat."""
    return 50 + 50 * max(-1.0, min(1.0, x / sat))


def _stock_greed(hist) -> tuple[float | None, dict]:
    """Per-stock fear/greed, modeled on CNN's index but from the stock's own
    price/volume history: RSI, momentum vs 125d MA, drawdown from 52w high,
    volatility spike, and up/down volume flow. Returns (0-100 greed, parts)."""
    closes = hist["Close"].dropna()
    if len(closes) < 60:
        return None, {}
    parts: dict[str, float] = {}

    rsi = _rsi(closes)
    if rsi is not None:
        parts["rsi"] = rsi

    # Momentum: price vs its 125-day MA (CNN's "market momentum", per stock).
    ma = closes.rolling(min(125, len(closes))).mean().iloc[-1]
    if ma:
        parts["momentum"] = _scale((closes.iloc[-1] / ma - 1) * 100, 20)

    # Drawdown from 52-week high (CNN's "price strength", per stock):
    # at the high = 100 (greed), 40%+ below it = 0 (deep-dip fear).
    dd_pct = (closes.iloc[-1] / closes.max() - 1) * 100  # <= 0
    parts["drawdown"] = max(0.0, min(100.0, 100 + dd_pct * 2.5))

    # Volatility: 20d realized vol vs its 100d norm (per-stock VIX analog).
    rets = closes.pct_change().dropna()
    v20, v100 = rets.tail(20).std(), rets.tail(100).std()
    if v100:
        parts["volatility"] = _scale((1 - v20 / v100) * 100, 60)

    # Volume flow: share of 20d volume on up days (breadth analog).
    if "Volume" in hist:
        vols = hist["Volume"].reindex(rets.index).fillna(0).tail(20)
        recent = rets.tail(20)
        total = vols.sum()
        if total:
            up_share = vols[recent > 0].sum() / total
            parts["volume"] = _scale((up_share - 0.5) * 100, 25)

    weights = CFG["stock_fng_weights"]
    used = {k: v for k, v in parts.items() if k in weights}
    if not used:
        return None, {}
    wsum = sum(weights[k] for k in used)
    greed = sum(v * weights[k] for k, v in used.items()) / wsum
    return round(greed, 1), {k: round(v, 1) for k, v in parts.items()}


def _rsi(closes, period: int = 14) -> float | None:
    """Cutler's RSI (simple moving average of gains/losses), 0-100."""
    if len(closes) <= period:
        return None
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(period).mean().iloc[-1]
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return round(100 - 100 / (1 + rs), 1)


def _pct_change(closes, trading_days: int) -> float | None:
    if len(closes) <= trading_days:
        return None
    prev = closes.iloc[-1 - trading_days]
    if not prev:
        return None
    return round((closes.iloc[-1] - prev) / prev * 100, 2)


def fetch_prices(ticker: str) -> dict:
    out = {
        "price": None, "currency": None,
        "chg_1d": None, "chg_1w": None, "chg_1m": None,
        "trailing_pe": None, "forward_pe": None,
        "target_price": None, "dividend_yield": None,
        "five_year_avg_yield": None, "rsi14": None,
        "stock_greed": None, "fng_parts": {},
        "domain": None,
    }
    t = yf.Ticker(ticker)
    try:
        # 1y history: the fear/greed composite needs the 125d MA and 52w high.
        hist = t.history(period="1y", auto_adjust=True)
        closes = hist["Close"].dropna()
        if len(closes):
            out["price"] = round(float(closes.iloc[-1]), 4)
            out["chg_1d"] = _pct_change(closes, 1)
            out["chg_1w"] = _pct_change(closes, 5)
            out["chg_1m"] = _pct_change(closes, 21)
            out["rsi14"] = _rsi(closes)
            out["stock_greed"], out["fng_parts"] = _stock_greed(hist)
    except Exception as e:
        log.warning("history failed for %s: %s", ticker, e)

    try:
        info = t.info or {}
        out["currency"] = info.get("currency")
        out["trailing_pe"] = info.get("trailingPE")
        out["forward_pe"] = info.get("forwardPE")
        out["target_price"] = info.get("targetMeanPrice")
        dy = info.get("dividendYield")
        # yfinance has flip-flopped between fraction (0.031) and percent (3.1);
        # normalize to percent.
        if dy is not None:
            out["dividend_yield"] = round(dy * 100, 2) if dy < 1 else round(dy, 2)
        out["five_year_avg_yield"] = info.get("fiveYearAvgDividendYield")
        # Company website domain, used for the logo (favicon) on the page.
        site = info.get("website") or ""
        if site:
            out["domain"] = site.split("//")[-1].split("/")[0]
        if out["price"] is None:
            out["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
    except Exception as e:
        log.warning("info failed for %s: %s", ticker, e)
    return out
