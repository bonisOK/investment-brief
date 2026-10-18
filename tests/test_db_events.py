from app import db


def ev(**kw):
    base = {"id": "xd:ADVANC:2026-10-20", "date": "2026-10-20", "kind": "xd", "code": "ADVANC"}
    return {**base, **kw}


def test_upsert_merges_and_nulls_never_erase(tmp_path):
    p = str(tmp_path / "e.db")
    db.upsert_events([ev(pay_date="2026-11-05", dps=None)], path=p)
    db.upsert_events([ev(pay_date=None, dps=4.5)], path=p)
    (got,) = db.events_between("2026-10-01", "2026-10-31", path=p)
    assert got["pay_date"] == "2026-11-05"
    assert got["dps"] == 4.5


def test_events_between_is_inclusive_and_sorted(tmp_path):
    p = str(tmp_path / "e.db")
    db.upsert_events([ev(id="a", date="2026-10-31"), ev(id="b", date="2026-10-01"),
                      ev(id="c", date="2026-11-01")], path=p)
    assert [e["id"] for e in db.events_between("2026-10-01", "2026-10-31", path=p)] == ["b", "a"]


def test_prune_upcoming_drops_rescheduled_dates_only(tmp_path):
    p = str(tmp_path / "e.db")
    db.upsert_events([
        ev(id="earn:APP:2026-10-01", kind="earnings", code="APP", date="2026-10-01"),  # past: keep
        ev(id="earn:APP:2026-11-04", kind="earnings", code="APP", date="2026-11-04"),  # moved: drop
        ev(id="earn:APP:2026-11-06", kind="earnings", code="APP", date="2026-11-06"),  # new date
        ev(id="earn:MU:2026-11-04", kind="earnings", code="MU", date="2026-11-04"),    # other code
    ], path=p)
    db.prune_upcoming("earnings", "APP", "2026-10-18", keep_ids={"earn:APP:2026-11-06"}, path=p)
    ids = {e["id"] for e in db.events_between("2026-01-01", "2026-12-31", path=p)}
    assert ids == {"earn:APP:2026-10-01", "earn:APP:2026-11-06", "earn:MU:2026-11-04"}


def test_shown_news_roundtrip(tmp_path):
    p = str(tmp_path / "e.db")
    db.mark_shown([{"key": "k1", "headline": "Fine hits margin"}], "2026-10-18T06:00:00+07:00", path=p)
    assert db.shown_keys(path=p) == {"k1"}
    assert db.recent_shown("2026-10-15T00:00:00+07:00", path=p) == ["Fine hits margin"]
    assert db.recent_shown("2026-10-19T00:00:00+07:00", path=p) == []
