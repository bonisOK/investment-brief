import json

from app import ai
from app.news import title_key


def row(code, titles):
    return {"dr_code": code, "company_name": code, "book": "growth", "chg_1d": -6.0,
            "current_value": 1000, "scores": {"label": "HOLD", "act_thb": 0},
            "news": [{"title": t, "link": f"L-{t}", "publisher": "P", "age_h": 5} for t in titles]}


def setup(monkeypatch, reply):
    prompts = []
    monkeypatch.setattr(ai, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(ai, "CALL_GAP_S", 0)
    monkeypatch.setattr(ai, "_generate", lambda p: prompts.append(p) or json.dumps(reply))
    return prompts


def test_brief_drops_already_shown_headlines_and_lists_recent_stories(monkeypatch):
    prompts = setup(monkeypatch, {"items": []})
    ai.daily_brief([row("MU", ["Micron guidance cut - Reuters", "Micron new fab"])], [],
                   seen_keys={title_key("Micron guidance cut")},
                   recent=["Guidance cut hits memory margins"])
    (p,) = prompts
    assert "Micron new fab" in p
    assert "Micron guidance cut" not in p
    assert "Guidance cut hits memory margins" in p


def test_brief_skips_the_call_when_everything_was_shown(monkeypatch):
    prompts = setup(monkeypatch, {"items": []})
    out = ai.daily_brief([row("MU", ["Old story"])], [], seen_keys={title_key("Old story")})
    assert prompts == [] and out == {"items": []}


def test_brief_items_carry_calendar_fields_and_no_summary(monkeypatch):
    setup(monkeypatch, {"summary": ["ignored"], "items": [
        {"id": "H1", "tickers": ["MU"], "headline": "Cut hurts", "implication": "bearish",
         "why": "w", "action": "Bad for MU", "urgency": "act",
         "calendar": True, "tag": "guidance"},
        {"id": "H2", "tickers": ["MU"], "headline": "Fab", "implication": "neutral",
         "why": "w", "action": "", "urgency": "fyi", "calendar": "yes", "tag": "bogus"}]})
    out = ai.daily_brief([row("MU", ["Micron guidance cut", "Micron new fab"])], [])
    assert "summary" not in out
    a, b = out["items"]
    assert (a["calendar"], a["tag"], a["age_h"], a["key"]) == (True, "guidance", 5, title_key("Micron guidance cut"))
    assert (b["calendar"], b["tag"]) == (False, "other")


def test_macro_items_get_a_market_scope(monkeypatch):
    setup(monkeypatch, {"items": [
        {"id": "M1", "tickers": [], "headline": "Fed shock", "implication": "bearish",
         "why": "w", "action": "", "urgency": "act", "calendar": True, "tag": "policy", "market": "us"},
        {"id": "M2", "tickers": [], "headline": "SET slides", "implication": "bearish",
         "why": "w", "action": "", "urgency": "watch", "calendar": True, "tag": "move"}]})
    macro = [{"title": "Fed hikes", "link": "a", "publisher": "R", "age_h": 2},
             {"title": "ตลาดหุ้นไทยร่วง", "link": "b", "publisher": "T", "age_h": 3}]
    out = ai.daily_brief([], macro)
    assert [i["scope"] for i in out["items"]] == ["us", "set"]
