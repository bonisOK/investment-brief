"""Orchestrates the full run: sheet -> resolve -> prices -> value -> news,
then batched AI analysis -> score -> LLM order sizing -> save."""
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from . import ai, db, news, prices, resolver, scoring, sentiment, sheets
from .config import APPS_SCRIPT_URL, CFG

log = logging.getLogger(__name__)
BKK = ZoneInfo("Asia/Bangkok")


def _refresh_sheet_prices():
    """Hit the sheet's Apps Script web app so updateMINTprices() runs and the
    Current Price / Current Value columns are fresh before we read them."""
    if not APPS_SCRIPT_URL:
        return
    try:
        r = requests.get(APPS_SCRIPT_URL, timeout=120, allow_redirects=True)
        log.info("Apps Script price refresh: HTTP %s", r.status_code)
        time.sleep(3)  # let the sheet finish recalculating
    except Exception as e:
        log.warning("Apps Script price refresh failed: %s", e)


def run_pipeline() -> dict:
    started = time.time()
    log.info("pipeline start")

    _refresh_sheet_prices()
    rows = sheets.read_growth() + sheets.read_dividend()
    fng = sentiment.fetch_fng()

    # Phase 1: per-name market data + news (no AI yet).
    results = []
    for row in rows:
        try:
            results.append(_process(row))
        except Exception as e:
            log.exception("failed on %s: %s", row["dr_code"], e)

    # Phase 2: batched AI analysis (a handful of Gemini calls, not one per name).
    analyses = ai.analyze_batch(results)
    for r in results:
        a = analyses.get(f"{r['book']}:{r['dr_code']}") or ai.fallback(r.get("chg_1d"))
        r["why_moved"] = a["why_moved"]
        r["so_what"] = a["so_what"]
        r["news_sentiment"] = a["sentiment"]
        r["scores"] = scoring.composite(r, fng["score"])

    results.sort(key=lambda r: r["scores"]["composite"], reverse=True)

    # Phase 3: LLM sizes the orders and explains them in one call
    # (rule-based amount + explanation otherwise).
    llm_amounts = ai.decide_amounts(results)
    for r in results:
        d = llm_amounts.get(f"{r['book']}:{r['dr_code']}")
        if d is not None:
            r["scores"]["act_thb"] = _cap_amount(d["amount"], r.get("buy_sell_thb"))
            r["scores"]["act_source"] = "llm"
            r["scores"]["act_price"] = _sane_price(d.get("limit_price"), r.get("price"))
        else:
            r["scores"]["act_source"] = "rule"
        r["scores"]["act_reason"] = (d or {}).get("reason") or scoring.reason_for(r)

    # Phase 4: "today's brief" — one call ranking the most important news.
    macro = news.fetch_macro_news() if CFG["news_brief"]["include_macro"] else []
    brief = ai.daily_brief(results, macro)
    if brief:
        _attach_brief_actions(brief, results)

    payload = {
        "brief": brief,
        "run_at": datetime.now(BKK).isoformat(),
        "run_at_display": datetime.now(BKK).strftime("%d %b %Y %H:%M"),
        "fng": fng,
        "rows": results,
        "duration_s": round(time.time() - started, 1),
    }
    run_id = db.save_run(payload)
    log.info("pipeline done: run %s, %d names, %.0fs", run_id, len(results),
             payload["duration_s"])
    return payload


def _attach_brief_actions(brief: dict, results: list[dict]) -> None:
    """Enrich each brief item with the concrete pending action per ticker
    (label, today's THB amount, limit price) pulled from the scored rows."""
    for it in brief.get("items", []):
        actions = []
        for code in it.get("tickers", []):
            for r in results:
                if r["dr_code"] == code:
                    s = r["scores"]
                    actions.append({
                        "code": code,
                        "book": r["book"],
                        "label": s["label"],
                        "act_thb": s.get("act_thb"),
                        "act_price": s.get("act_price"),
                        "currency": r.get("currency"),
                    })
        it["actions"] = actions


def _sane_price(limit: float | None, current: float | None) -> float | None:
    """Keep the LLM's limit price only if it's within 15% of the live price."""
    if not limit or not current:
        return None
    if abs(limit - current) / current > 0.15:
        return None
    return round(limit, 2)


def _cap_amount(amt: float, gap: float | None) -> float | None:
    """Sanity-cap the LLM's order size at 1.5x the gap to target and round
    to hundreds; tiny/zero -> None (no chip shown)."""
    if gap:
        limit = abs(gap) * 1.5
        amt = max(-limit, min(limit, amt))
    amt = round(amt, -2)
    return amt or None


def _process(row: dict) -> dict:
    ident = resolver.resolve(row["dr_code"], thai=row.get("is_thai", False))
    ticker, company = ident["real_ticker"], ident["company_name"]
    log.info("processing %s -> %s (%s)", row["dr_code"], ticker, company)

    px = prices.fetch_prices(ticker)
    val = valuation_safe(ticker, px)
    headlines = news.fetch_news(company, is_thai=row.get("is_thai", False),
                                symbol=row["dr_code"] if row.get("is_thai") else None)
    return {**row, **ident, **px, **val, "news": headlines}


def valuation_safe(ticker: str, px: dict) -> dict:
    from . import valuation
    try:
        return valuation.fetch_valuation(ticker, px)
    except Exception as e:
        log.warning("valuation failed for %s: %s", ticker, e)
        return {"dcf_value": None, "undervalued_pct": None, "value_basis": "error"}
