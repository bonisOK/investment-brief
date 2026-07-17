"""FastAPI app: serves the brief page, hosts the twice-daily scheduler."""
import json
import logging
import secrets
import threading
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import db, pipeline
from .config import APP_PASSWORD, CFG

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Investment Brief")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic(auto_error=False)

_run_lock = threading.Lock()


def _guard(credentials: HTTPBasicCredentials | None = Depends(security)):
    """Optional single-password basic auth (username ignored)."""
    if not APP_PASSWORD:
        return
    ok = credentials is not None and secrets.compare_digest(
        credentials.password.encode(), APP_PASSWORD.encode())
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )


def _run_once():
    if not _run_lock.acquire(blocking=False):
        log.info("pipeline already running, skipping")
        return
    try:
        pipeline.run_pipeline()
    except Exception:
        log.exception("pipeline run failed")
    finally:
        _run_lock.release()


@app.on_event("startup")
def startup():
    sched = BackgroundScheduler(timezone=CFG["schedule"]["timezone"])
    for hhmm in CFG["schedule"]["times"]:
        hour, minute = hhmm.split(":")
        sched.add_job(_run_once, CronTrigger(hour=int(hour), minute=int(minute)))
    sched.start()
    log.info("scheduler started: %s %s", CFG["schedule"]["times"],
             CFG["schedule"]["timezone"])
    # First boot with an empty DB: build a snapshot right away so the page has data.
    if db.latest_run() is None:
        threading.Thread(target=_run_once, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, _=Depends(_guard)):
    snap = db.latest_run()
    if snap is None:
        return HTMLResponse(
            "<meta http-equiv='refresh' content='15'>"
            "<body style='font-family:sans-serif;padding:2rem'>"
            "<h2>First snapshot is being built…</h2>"
            "<p>This page refreshes automatically.</p></body>")
    growth = [r for r in snap["rows"] if r["book"] == "growth"]
    dividend = [r for r in snap["rows"] if r["book"] == "dividend"]
    return templates.TemplateResponse(request, "index.html", {
        "snap": snap,
        "growth": growth,
        "dividend": dividend,
        "started": request.query_params.get("started") == "1",
        "static_mode": False,
        # DR code -> company website domain, for logos in the brief card.
        "ticker_domains": {r["dr_code"]: r["domain"]
                           for r in snap["rows"] if r.get("domain")},
        "chart_json": json.dumps(_chart_data(snap["rows"]), ensure_ascii=False),
    })


def _chart_data(rows: list[dict]) -> dict:
    # Movers: every stock with a price, best to worst.
    movers = sorted((r for r in rows if r.get("chg_1d") is not None),
                    key=lambda r: r["chg_1d"], reverse=True)
    def pack(items, value):
        return {"labels": [r["dr_code"] for r in items],
                "values": [value(r) for r in items]}

    # STRONG BUY / SELL are no longer charted — the template renders them as
    # HTML action lists so the ฿ amounts respond to the privacy toggle.
    return {"movers": pack(movers, lambda r: r["chg_1d"])}


@app.post("/run")
@app.get("/run")
def trigger_run(_=Depends(_guard)):
    threading.Thread(target=_run_once, daemon=True).start()
    return RedirectResponse("/?started=1", status_code=303)


@app.get("/api/latest")
def api_latest(_=Depends(_guard)):
    snap = db.latest_run()
    return JSONResponse(snap or {"status": "no data yet"})


@app.get("/health")
def health():
    return {"ok": True}
