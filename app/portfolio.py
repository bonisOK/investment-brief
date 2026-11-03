"""Growth and Dividend tabs: rebuild each book's daily value from its trade
log, set it against a shadow index (every deposit buys the index on the same
day, in ฿), and work out summary, allocation, contribution, buy win rate and
sell check. `compute_book` is pure; `build` does the fetching."""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import db, ledger, pricehist, resolver, sheets

log = logging.getLogger(__name__)
BKK = ZoneInfo("Asia/Bangkok")
DEFAULT_INDEXES = {"growth": ["spx", "ndx"], "dividend": ["spx", "set"]}


def shadow(days: list[date], deps: list[tuple[date, float]],
           px: list[float | None]) -> list[float | None]:
    """Value of 'every deposit bought the index on its date'. A deposit made
    on a day without a price (weekend, or before the series starts) buys at
    the next available close."""
    units, pending, out = 0.0, 0.0, []
    j = 0
    deps = sorted(deps)
    for i, d in enumerate(days):
        while j < len(deps) and deps[j][0] <= d:
            pending += deps[j][1]
            j += 1
        p = px[i]
        if p:
            units += pending / p
            pending = 0.0
            out.append(units * p)
        else:
            out.append(None)
    return out


def compute_book(txns: list[ledger.Txn], days: list[date],
                 prices: dict[str, list[float | None]],
                 indexes: dict[str, list[float | None]],
                 names: dict[str, str] | None = None,
                 domains: dict[str, str] | None = None,
                 sources: dict[str, str] | None = None) -> dict:
    """Everything one tab shows. `prices[code]` is aligned with `days` (฿);
    its last value is today's price."""
    names, domains, sources = names or {}, domains or {}, sources or {}
    held, cash, warns = ledger.replay(txns, days)
    deps = [(d, a) for d, a in ledger.deposits(txns) if d <= days[-1]]

    # Daily value. A missing price falls back to the last trade price seen.
    last_trade: dict[str, float] = {}
    you, money, m = [], [], 0.0
    di, ti = 0, 0
    dep_sorted = sorted(deps)
    tx_sorted = [t for t in txns if t.kind in ("buy", "sell") and t.price]
    for i, d in enumerate(days):
        while ti < len(tx_sorted) and tx_sorted[ti].date <= d:
            last_trade[tx_sorted[ti].code] = tx_sorted[ti].price
            ti += 1
        while di < len(dep_sorted) and dep_sorted[di][0] <= d:
            m += dep_sorted[di][1]
            di += 1
        v = cash[i]
        for code, sh in held[i].items():
            p = (prices.get(code) or [None] * len(days))[i] or last_trade.get(code)
            v += sh * (p or 0.0)
        you.append(round(v))
        money.append(round(m))

    series = {"you": you, "money": money}
    for k, px in indexes.items():
        sh = shadow(days, deps, px)
        series[k] = [round(x) if x is not None else None for x in sh]

    now_px = {c: (p[-1] if p else None) or last_trade.get(c) for c, p in prices.items()}
    for c, p in last_trade.items():
        now_px.setdefault(c, p)
    held_now = held[-1]
    stats = ledger.ticker_stats([t for t in txns if t.date <= days[-1]], held_now)

    # Summary
    stock_value = sum(sh * (now_px.get(c) or 0.0) for c, sh in held_now.items())
    realized = sum(s["realized"] for s in stats.values())
    dividends = sum(s["dividends"] for s in stats.values())
    unrealized = sum(s["held_shares"] * (now_px.get(c) or 0.0) - s["held_cost"]
                     for c, s in stats.items() if s["held_shares"])
    total = stock_value + cash[-1]
    deposited = sum(a for _, a in deps)
    sold_out = [c for c, s in stats.items() if not s["held_shares"] and s["bought_shares"]]
    summary = {
        "total": round(total), "cash": round(cash[-1]), "deposits": round(deposited),
        "n_deposits": len(deps), "realized": round(realized), "dividends": round(dividends),
        "unrealized": round(unrealized), "stock_value": round(stock_value),
        "held_count": len(held_now), "sold_count": len(sold_out),
    }

    alloc = sorted(({"code": c, "value": round(sh * (now_px.get(c) or 0.0))}
                    for c, sh in held_now.items()), key=lambda a: -a["value"])

    contrib = sorted(({"code": c, "v": round(s["realized"] + s["dividends"] + (
        s["held_shares"] * (now_px.get(c) or 0.0) - s["held_cost"] if s["held_shares"] else 0.0))}
        for c, s in stats.items() if s["bought_shares"] or s["dividends"]), key=lambda x: -x["v"])

    # Lots and sells, judged against today's price.
    bad = ledger.bad_price_rows(txns)
    lots, sells, trades = [], [], []
    for t in txns:
        if t.kind not in ("buy", "sell") or t.date > days[-1]:
            continue
        trades.append({"date": t.date.isoformat(), "side": "Buy" if t.kind == "buy" else "Sell",
                       "code": t.code, "thb": round(abs(t.cash))})
        now = now_px.get(t.code)
        if t.row in bad or not now:
            continue
        if t.kind == "buy":
            cost = -t.cash
            pl = t.shares * now - cost
            lots.append({"date": t.date.isoformat(), "code": t.code, "thb": round(cost),
                         "price": t.price, "now": round(now, 4), "pl": round(pl),
                         "ret": round(pl / cost, 4) if cost else 0.0})
        else:
            sh = -t.shares
            diff = (now - t.price) * sh
            sells.append({"date": t.date.isoformat(), "code": t.code, "thb": round(t.cash),
                          "price": t.price, "now": round(now, 4), "diff": round(diff),
                          "since": round(now / t.price - 1, 4)})
    lots.reverse()
    sells.reverse()
    if bad:
        warns.append(f"{len(bad)} trade row(s) left out of win rate / sell check: "
                     f"price looks swapped with shares (rows {', '.join(map(str, sorted(bad)))})")

    codes = sorted(set(stats))
    return {
        "days": [d.isoformat() for d in days],
        "series": series,
        "summary": summary,
        "alloc": alloc,
        "contrib": contrib,
        "lots": lots,
        "sells": sells,
        "trades": trades,
        "names": {c: names.get(c, c) for c in codes},
        "domains": {c: domains[c] for c in codes if domains.get(c)},
        "est": sorted(c for c in codes if sources.get(c) == "est" and c in held_now),
        "warnings": warns,
    }


# ---------- glue ----------

def _book_inputs(book: str, results: list[dict], fetch: pricehist.Fetcher, today: date):
    raw = sheets.read_transactions(book)
    txns, warns = ledger.parse(raw)
    if not txns:
        raise ValueError("the trade log is empty")
    days = ledger.business_days(min(t.date for t in txns), today)

    by_code: dict[str, dict] = {}
    for r in results:  # rows the brief already resolved (any book)
        by_code.setdefault(r["dr_code"], r)
    points: dict[str, list[tuple[date, float]]] = {}
    for t in txns:
        if t.kind in ("buy", "sell") and t.price:
            points.setdefault(t.code, []).append((t.date, t.price))
    bad = ledger.bad_price_rows(txns)
    good = {}
    for t in txns:
        if t.kind in ("buy", "sell") and t.price and t.row not in bad:
            good.setdefault(t.code, []).append((t.date, t.price))
    points = {c: good.get(c) or pts for c, pts in points.items()}

    names, domains, sources, prices = {}, {}, {}, {}
    for code in sorted(points):
        r = by_code.get(code)
        if r:
            underlying, names[code] = r.get("real_ticker"), r.get("company_name") or code
            if r.get("domain"):
                domains[code] = r["domain"]
        else:
            try:
                ident = resolver.resolve(code, thai=not ledger.is_dr(code))
                underlying, names[code] = ident["real_ticker"], ident["company_name"]
            except Exception:
                underlying, names[code] = None, code
        pts = list(points[code])
        sheet_px = r.get("current_price") if (r and r.get("book") == book) else None
        if sheet_px:
            pts.append((today, sheet_px))  # also anchors the fitted ratio
        series, sources[code] = fetch.ticker(code, underlying, pts)
        aligned = pricehist.align(series, days)
        if sheet_px:  # today's price = the sheet's, so totals match it
            aligned[-1] = sheet_px
        prices[code] = aligned

    idx, labels, levels = {}, {}, {}
    for k in pricehist.INDEXES:
        series, label = fetch.index(k)
        idx[k] = pricehist.align(series, days)
        if label:
            labels[k] = label
        levels[k] = [round(v, 2) if v is not None else None
                     for v in pricehist.align(fetch.level(k), days)]
    return txns, warns, days, prices, idx, names, domains, sources, labels, levels


def reconcile(book: str, held_now: dict[str, float], stock_value: float,
              results: list[dict]) -> dict:
    """Rebuilt shares vs the sheet's Current Shares for every ticker the
    sheet lists, and (growth) rebuilt stock value vs the sheet's Current Value.
    The page shows a warning badge when either is off."""
    rows = [r for r in results if r.get("book") == book]
    mism = sorted(r["dr_code"] for r in rows
                  if abs((r.get("current_shares") or 0.0) - held_now.get(r["dr_code"], 0.0)) > 0.5)
    out = {"checked": len(rows), "share_mismatches": mism, "value_diff_pct": None}
    if book == "growth":
        sheet_val = sum(r.get("current_value") or 0.0 for r in rows)
        if sheet_val:
            out["value_diff_pct"] = round((stock_value - sheet_val) / sheet_val * 100, 2)
    return out


def build(results: list[dict], today: date | None = None) -> dict:
    today = today or datetime.now(BKK).date()
    fetch = pricehist.Fetcher(date(today.year - 1, today.month, 1))
    out = {"default_indexes": DEFAULT_INDEXES}
    for book in ("growth", "dividend"):
        try:
            txns, warns, days, prices, idx, names, domains, sources, labels, levels = _book_inputs(
                book, results, fetch, today)
            p = compute_book(txns, days, prices, idx, names, domains, sources)
            p["index_labels"] = labels
            p["index_levels"] = levels   # the Trades chart: index points, not ฿
            p["warnings"] = warns + p["warnings"]
            p["sources"] = {s: sum(1 for v in sources.values() if v == s)
                            for s in ("dr", "underlying", "est")}
            held_now, _, _ = ledger.replay(txns, days[-1:])
            p["reconcile"] = reconcile(book, held_now[-1], p["summary"]["stock_value"], results)
            out[book] = p
            db.save_portfolio_snapshot(today.isoformat(), book, p["summary"]["total"],
                                       p["summary"]["cash"], p["summary"]["stock_value"])
            log.info("portfolio %s: %d days, %d tickers, prices %s, value diff vs sheet %s%%, "
                     "%d share mismatches, %d warnings", book, len(days), len(prices),
                     p["sources"], p["reconcile"].get("value_diff_pct"),
                     len(p["reconcile"]["share_mismatches"]), len(p["warnings"]))
        except Exception as e:
            log.exception("portfolio %s failed", book)
            out[book] = {"error": f"{type(e).__name__}: {e}"}
    return out
