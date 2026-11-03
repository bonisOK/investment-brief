"""The page renders with the portfolio tabs, both with data and with a failed book."""
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app import calendar_view
from app.main import _chart_data, port_json
from tests.test_portfolio import _book


def _render(port):
    snap = {"portfolio": port, "rows": [], "brief": None, "run_at_display": "x",
            "fng": {"score": 50, "label": "Neutral"}}
    env = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "app" / "templates"))
    return env.get_template("index.html").render(
        snap=snap, growth=[], dividend=[], started=False, static_mode=True, ticker_domains={},
        cal_json=calendar_view.cal_json({}), chart_json=json.dumps(_chart_data([])),
        port_json=port_json(snap))


def test_tabs_and_payload_are_on_the_page():
    html = _render({"growth": _book(), "dividend": {"error": "boom"}})
    for needle in ('id="tab-brief"', 'id="tab-growth"', 'id="tab-dividend"', 'href="#growth"',
                   "lightweight-charts@4.2.0", '"AAA01"', '"error": "boom"'):
        assert needle in html


def test_script_cannot_be_closed_by_data():
    html = _render({"growth": {"error": "</script><b>x"}})
    assert "</script><b>x" not in html and "<\\/script><b>x" in html
