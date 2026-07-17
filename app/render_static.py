"""Render the dashboard as a static HTML file (for GitHub Actions + Pages).

Usage:
    python -m app.render_static             # run the full pipeline, then render
    python -m app.render_static --from-db   # render the latest stored snapshot
"""
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import db
from .main import _chart_data

OUT = Path(__file__).resolve().parent.parent / "site" / "index.html"


def main() -> int:
    if "--from-db" in sys.argv:
        snap = db.latest_run()
        if snap is None:
            print("no snapshot in DB; run without --from-db first", file=sys.stderr)
            return 1
    else:
        from . import pipeline
        snap = pipeline.run_pipeline()

    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"))
    html = env.get_template("index.html").render(
        snap=snap,
        growth=[r for r in snap["rows"] if r["book"] == "growth"],
        dividend=[r for r in snap["rows"] if r["book"] == "dividend"],
        started=False,
        static_mode=True,
        ticker_domains={r["dr_code"]: r["domain"]
                        for r in snap["rows"] if r.get("domain")},
        chart_json=json.dumps(_chart_data(snap["rows"]), ensure_ascii=False),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(html):,} bytes, run {snap['run_at_display']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
