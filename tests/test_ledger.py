"""Trade-log replay and the sheet's realized-gain method, on tiny ledgers."""
from datetime import date

from app import ledger


def row(d, kind, code="", shares=None, price=None, final=None, before=None):
    return {"date": d, "type": kind, "ticker": code, "shares": shares, "price": price,
            "before_com": before, "final": final}


ROWS = [
    row("2/27/2026", "Deposit", "CASH", 1000, None, 1000.0),
    row("3/2/2026", "Buy", "AAA01", 10, 10.0, -101.0),
    row("3/3/2026 0:00:00", "Buy", "AAA01", 10, 20.0, -199.0),
    row("3/4/2026 11:03:21", "Sell", "AAA01", -5, 30.0, 149.0),
    row("3/5/2026", "Dividend", "AAA01", None, None, 7.0),
    row("3/7/2026", "Deposit", "", None, None, None, 500.0),   # Saturday, blank ticker
    row("", "", "", None, None, None),                           # blank row
]


def test_parse_dates_kinds_and_signs():
    txns, warns = ledger.parse(ROWS)
    assert warns == []
    assert [t.kind for t in txns] == ["deposit", "buy", "buy", "sell", "dividend", "deposit"]
    assert txns[2].date == date(2026, 3, 3) and txns[3].date == date(2026, 3, 4)
    assert txns[3].shares == -5 and txns[1].shares == 10
    assert txns[5].cash == 500.0 and txns[5].code == ""


def test_replay_shares_and_cash_weekend_deposit_lands_monday():
    txns, _ = ledger.parse(ROWS)
    days = ledger.business_days(date(2026, 2, 27), date(2026, 3, 9))
    held, cash, warns = ledger.replay(txns, days)
    assert days[-1] == date(2026, 3, 9) and date(2026, 3, 7) not in days
    assert held[days.index(date(2026, 3, 4))] == {"AAA01": 15}
    assert cash[days.index(date(2026, 3, 6))] == 1000 - 101 - 199 + 149 + 7
    assert cash[-1] == 1000 - 101 - 199 + 149 + 7 + 500
    assert warns == []


def test_ticker_stats_matches_sheet_method():
    txns, _ = ledger.parse(ROWS)
    st = ledger.ticker_stats(txns, {"AAA01": 15})["AAA01"]
    # running average after both buys: 300 / 20 = 15; sell valued before fee
    assert st["cost_per_share"] == 15.0
    assert st["realized"] == 5 * 30.0 - 5 * 15             # 75
    assert st["dividends"] == 7 and st["held_cost"] == 15 * 15


def test_running_average_resets_after_full_sell_and_survives_oversell():
    rows = [row("3/2/2026", "Buy", "S", 10, 2.0, -20.0),
            row("3/3/2026", "Sell", "S", -10, 3.0, 29.0),    # +10, average resets
            row("3/4/2026", "Buy", "S", 10, 6.0, -60.0),
            row("3/5/2026", "Sell", "S", -10, 7.0, 69.0),    # +10 at the new average 6
            row("3/6/2026", "Buy", "A", 10, 1.0, -10.0),
            row("3/9/2026", "Sell", "A", -15, 2.0, 30.0)]    # oversell keeps average 1
    st = ledger.ticker_stats(ledger.parse(rows)[0], {})
    assert st["S"]["realized"] == 20.0
    assert st["A"]["realized"] == 15 * 2.0 - 15 * 1.0


def test_oversell_caps_at_zero_and_warns():
    rows = [row("3/2/2026", "Buy", "BBB", 10, 1.0, -10.0),
            row("3/3/2026", "Sell", "BBB", -10, 1.0, 10.0),
            row("3/3/2026", "Sell", "BBB", -10, 1.0, 10.0)]
    txns, _ = ledger.parse(rows)
    held, cash, warns = ledger.replay(txns, ledger.business_days(date(2026, 3, 2), date(2026, 3, 4)))
    assert held[-1] == {} and cash[-1] == 10.0
    assert len(warns) == 1 and "BBB" in warns[0]


def test_bad_price_rows_catches_swapped_cells():
    rows = [row("3/2/2026", "Buy", "GEV23", 12557, 3.16, -39743.0),
            row("3/3/2026", "Buy", "GEV23", 3.16, 12557, -39746.0),
            row("3/4/2026", "Buy", "GEV23", 9787, 3.1, -30388.0)]
    txns, _ = ledger.parse(rows)
    assert ledger.bad_price_rows(txns) == {3}


def test_is_dr():
    assert ledger.is_dr("NVDA01") and ledger.is_dr("PEP80")
    assert not ledger.is_dr("GULF") and not ledger.is_dr("")
