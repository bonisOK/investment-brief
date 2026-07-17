"""Composite 0-100 suggestion score. Higher = more attractive to buy."""
from .config import CFG


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _to_score(signal: float) -> float:
    """signal in [-1, +1] -> score in [0, 100]."""
    return 50 + 50 * _clamp(signal)


def drift_score(row: dict) -> float:
    """Signed THB gap to target: growth = Target Cost − Current Value;
    dividend = the sheet's own 'Real Have to buy'. Positive -> buy more."""
    amt = row.get("buy_sell_thb")
    if amt is None:
        return 50.0
    return _to_score(amt / CFG["drift_saturation_thb"][row["book"]])


def value_score(undervalued_pct: float | None) -> float:
    if undervalued_pct is None:
        return 50.0
    return _to_score(undervalued_pct / CFG["value_saturation_pct"])


def fng_score(row: dict, market_fng: int) -> float:
    """Company-specific fear/greed, contrarian (fear -> high buy score).
    Blend of the market-wide CNN gauge (dampened for Thai names — it's a US
    index) and the stock's own 5-component greed composite (RSI, momentum,
    drawdown, volatility, volume): deep-dip fear = buy signal."""
    mix = CFG["fng_mix"]
    market = 100 - market_fng
    if row.get("is_thai"):
        market = 50 + (market - 50) * CFG["thai_fng_dampen"]
    greed = row.get("stock_greed")
    if greed is None:
        greed = row.get("rsi14")
    stock = (100 - greed) if greed is not None else market
    return mix["market"] * market + mix["stock"] * stock


def news_score(sentiment: float | None) -> float:
    return _to_score(sentiment or 0.0)


def suggested_amount(label: str, buy_sell_thb: float | None) -> float | None:
    """THB amount to act on now: a conviction-scaled slice of the gap to
    target. None when the label is HOLD, the gap is missing, or the gap
    direction disagrees with the label (e.g. SELL label but under-weight)."""
    if buy_sell_thb is None:
        return None
    frac = CFG["action_fractions"].get(label, 0)
    if not frac:
        return None
    buyish = label in ("STRONG BUY", "ADD")
    if (buyish and buy_sell_thb <= 0) or (not buyish and buy_sell_thb >= 0):
        return None
    return round(buy_sell_thb * frac, -2)


def composite(row: dict, fng: int) -> dict:
    w = CFG["weights"]
    subs = {
        "drift": round(drift_score(row), 1),
        "value": round(value_score(row.get("undervalued_pct")), 1),
        "fng": round(fng_score(row, fng), 1),
        "news": round(news_score(row.get("news_sentiment")), 1),
    }
    total = round(sum(subs[k] * w[k] for k in subs), 1)
    label = label_for(total)
    return {**subs, "composite": total, "label": label,
            "act_thb": suggested_amount(label, row.get("buy_sell_thb"))}


def reason_for(row: dict) -> str:
    """Compact rule-based explanation of a suggestion, from its inputs."""
    parts = []
    gap = row.get("buy_sell_thb")
    if gap and abs(gap) >= 1000:
        parts.append(f"{'under' if gap > 0 else 'over'} target by ฿{abs(gap):,.0f}")
    uv = row.get("undervalued_pct")
    if uv is not None and abs(uv) >= 5:
        parts.append(f"{abs(uv):.0f}% {'under' if uv > 0 else 'over'}valued"
                     f" ({row.get('value_basis', '')})")
    greed = row.get("stock_greed")
    if greed is not None:
        if greed <= 35:
            parts.append(f"stock in fear (F&G {greed:.0f})")
        elif greed >= 65:
            parts.append(f"stock in greed (F&G {greed:.0f})")
    sent = row.get("news_sentiment") or 0
    if sent >= 0.25:
        parts.append("positive news")
    elif sent <= -0.25:
        parts.append("negative news")
    return " · ".join(parts) or "all signals near neutral"


def label_for(score: float) -> str:
    for bucket in CFG["buckets"]:
        if score >= bucket["min"]:
            return bucket["label"]
    return CFG["buckets"][-1]["label"]
