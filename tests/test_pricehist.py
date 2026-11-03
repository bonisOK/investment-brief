from datetime import date

from app import pricehist

D = [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]


def test_align_carries_last_close_forward():
    s = {D[0]: 10.0, D[2]: 12.0}
    assert pricehist.align(s, D) == [10.0, 10.0, 12.0, 12.0, 12.0]
    assert pricehist.align({D[1]: 5.0}, D)[0] is None


def test_fit_ratio_recovers_known_ratio_and_ignores_one_bad_row():
    base = {d: 100.0 + i for i, d in enumerate(D)}
    pts = [(D[0], 1.0), (D[1], 1.01), (D[3], 1.03), (D[4], 999.0)]
    assert abs(pricehist.fit_ratio(pts, base) - 0.01) < 1e-9


def test_multiply_uses_last_rate_on_or_before():
    px = {D[0]: 2.0, D[2]: 3.0}
    fx = {D[0]: 30.0}
    assert pricehist.multiply(px, fx) == {D[0]: 60.0, D[2]: 90.0}


class FakeFetcher(pricehist.Fetcher):
    def __init__(self, data):
        super().__init__(D[0])
        self.data = data

    def history(self, symbol):
        return self.data.get(symbol, ({}, None))


def test_ticker_prefers_own_dr_history_then_underlying_then_trades():
    own = {d: 5.0 for d in D}
    f = FakeFetcher({"NVDA01.BK": (own, "THB")})
    assert f.ticker("NVDA01", "NVDA", []) == (own, "dr")

    usd = {d: 100.0 for d in D}
    f = FakeFetcher({"NVDA": (usd, "USD"), "THB=X": ({D[0]: 32.0}, "THB")})
    series, src = f.ticker("NVDA01", "NVDA", [(D[1], 32.0)])  # ratio 0.01
    assert src == "underlying" and abs(series[D[4]] - 32.0) < 1e-9

    series, src = FakeFetcher({}).ticker("IREN", None, [(D[1], 1678.75)])
    assert src == "est" and series == {D[1]: 1678.75}


def test_set_falls_back_until_a_source_has_data(monkeypatch):
    f = FakeFetcher({"TDEX.BK": ({date(2026, 3, d): 30.0 + d for d in range(1, 31)}, "THB")})
    monkeypatch.setattr(f, "yahoo_chart", lambda s: {})
    monkeypatch.setattr(f, "set_api", lambda s: {})
    series, label = f.index("set")
    assert len(series) == 30 and "TDEX" in label
    f2 = FakeFetcher({"^SET.BK": ({date(2026, 3, d): 1400.0 for d in range(1, 31)}, "THB")})
    assert f2.index("set")[1] == "SET index, price only"


def test_parse_set_api_quotes_defensively():
    payload = {"quotations": [{"datetime": "2026-03-02T00:00:00+07:00", "price": 1401.5},
                              {"date": "2026-03-03", "value": 1399.0}, {"datetime": "bad"}, "x"]}
    assert pricehist._parse_quotes(payload) == {date(2026, 3, 2): 1401.5, date(2026, 3, 3): 1399.0}


def test_level_uses_index_points_then_etf_fallback():
    pts = {date(2026, 3, d): 6800.0 for d in range(1, 31)}
    spy = {date(2026, 3, d): 680.0 for d in range(1, 31)}
    assert FakeFetcher({"^GSPC": (pts, "USD"), "SPY": (spy, "USD")}).level("spx") == pts
    assert FakeFetcher({"SPY": (spy, "USD")}).level("spx") == spy
