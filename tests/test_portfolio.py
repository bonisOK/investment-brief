"""Shadow index, daily value, summary, win rate and sell check on a tiny book."""
from datetime import date

from app import db, ledger, portfolio


def row(d, kind, code="", shares=None, price=None, final=None):
    return {"date": d, "type": kind, "ticker": code, "shares": shares, "price": price,
            "before_com": None, "final": final}


def test_shadow_of_flat_index_equals_money_in():
    days = ledger.business_days(date(2026, 3, 2), date(2026, 3, 6))
    deps = [(date(2026, 3, 1), 100.0), (date(2026, 3, 4), 50.0)]   # first one on a Sunday
    assert portfolio.shadow(days, deps, [10.0] * 5) == [100, 100, 150, 150, 150]


def test_shadow_buys_at_first_available_price():
    days = ledger.business_days(date(2026, 3, 2), date(2026, 3, 4))
    out = portfolio.shadow(days, [(date(2026, 3, 2), 100.0)], [None, 10.0, 20.0])
    assert out == [None, 100.0, 200.0]


BOOK = [
    row("3/2/2026", "Deposit", "CASH", None, None, 1000.0),
    row("3/2/2026", "Buy", "AAA01", 10, 10.0, -100.0),      # lot: now 15 -> +50
    row("3/3/2026", "Buy", "AAA01", 10, 20.0, -200.0),      # lot: now 15 -> -50
    row("3/4/2026", "Sell", "AAA01", -5, 12.0, 60.0),       # now 15: sold early, gave up 15
    row("3/4/2026", "Buy", "BBB", 1, 100.0, -100.0),        # held at 80
    row("3/5/2026", "Dividend", "AAA01", None, None, 4.0),
]


def _book():
    txns, _ = ledger.parse(BOOK)
    days = ledger.business_days(date(2026, 3, 2), date(2026, 3, 6))
    prices = {"AAA01": [10.0, 20.0, 12.0, 14.0, 15.0], "BBB": [None, None, 100.0, 90.0, 80.0]}
    idx = {"spx": [1.0, 1.0, 1.0, 1.0, 2.0]}
    return portfolio.compute_book(txns, days, prices, idx, names={"AAA01": "Aaa"},
                                  domains={"BBB": "bbb.com"}, sources={"BBB": "est"})


def test_daily_value_summary_and_shadow():
    p = _book()
    s = p["summary"]
    cash = 1000 - 100 - 200 + 60 - 100 + 4
    assert s["cash"] == cash and s["deposits"] == 1000 and s["n_deposits"] == 1
    assert p["series"]["you"][0] == 1000 - 100 + 10 * 10
    assert p["series"]["you"][-1] == round(cash + 15 * 15 + 80)
    assert s["total"] == p["series"]["you"][-1]
    assert s["realized"] == round(5 * 12.0 - 5 * 15) and s["dividends"] == 4
    assert s["unrealized"] == round(15 * 15 - 15 * 15 + 80 - 100)
    assert p["series"]["money"] == [1000] * 5
    assert p["series"]["spx"] == [1000, 1000, 1000, 1000, 2000]
    assert [a["code"] for a in p["alloc"]] == ["AAA01", "BBB"]
    assert p["est"] == ["BBB"] and p["domains"] == {"BBB": "bbb.com"}


def test_lots_sells_contribution():
    p = _book()
    lots = {(l["date"], l["code"]): l for l in p["lots"]}
    assert lots[("2026-03-02", "AAA01")]["pl"] == 50
    assert lots[("2026-03-03", "AAA01")]["pl"] == -50
    assert lots[("2026-03-04", "BBB")]["ret"] == -0.2
    assert p["lots"][0]["date"] == "2026-03-04"          # newest first
    (sell,) = p["sells"]
    assert sell["diff"] == 15 and sell["thb"] == 60
    contrib = {c["code"]: c["v"] for c in p["contrib"]}
    assert contrib == {"AAA01": round(-15 + 4 + 0), "BBB": -20}   # sell 5 x 12 - 5 x 15 = -15
    assert {t["side"] for t in p["trades"]} == {"Buy", "Sell"} and len(p["trades"]) == 4


def test_reconcile_flags_share_mismatch_and_value_gap():
    results = [{"book": "growth", "dr_code": "AAA01", "current_shares": 15, "current_value": 200.0},
               {"book": "growth", "dr_code": "CCC", "current_shares": 3, "current_value": 100.0}]
    out = portfolio.reconcile("growth", {"AAA01": 15}, 330.0, results)
    assert out["share_mismatches"] == ["CCC"] and out["value_diff_pct"] == 10.0


def test_snapshot_roundtrip():
    db.save_portfolio_snapshot("2026-11-03", "growth", 10.0, 1.0, 9.0)
    db.save_portfolio_snapshot("2026-11-03", "growth", 11.0, 1.0, 10.0)
    assert db.portfolio_snapshots("growth")[-1]["total"] == 11.0
