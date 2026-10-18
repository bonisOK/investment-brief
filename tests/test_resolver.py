from app import db, resolver


def test_override_beats_a_stale_cached_row(monkeypatch):
    # The CI cache resolved APPL03 to Apple before the override existed.
    db.cache_ticker("APPL03", "AAPL", "Apple Inc.", "NASDAQ")
    monkeypatch.setitem(resolver.OVERRIDES, "APPL", "APP")
    monkeypatch.setattr(resolver, "_search", lambda q: [
        {"symbol": "APP", "quoteType": "EQUITY", "longname": "AppLovin Corporation",
         "exchange": "NMS", "exchDisp": "NASDAQ"}])

    got = resolver.resolve("APPL03")

    assert got["real_ticker"] == "APP"
    assert got["company_name"] == "AppLovin Corporation"
    assert db.get_cached_ticker("APPL03")["real_ticker"] == "APP"


def test_cache_still_used_when_it_agrees_with_override(monkeypatch):
    db.cache_ticker("COSTCO19", "COST", "Costco", "NASDAQ")
    monkeypatch.setitem(resolver.OVERRIDES, "COSTCO", "COST")
    monkeypatch.setattr(resolver, "_search", lambda q: (_ for _ in ()).throw(AssertionError("no search")))
    assert resolver.resolve("COSTCO19")["real_ticker"] == "COST"


def test_applovin_override_is_configured():
    from app.config import OVERRIDES
    assert OVERRIDES.get("APPL") == "APP"
