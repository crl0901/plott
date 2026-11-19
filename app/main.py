"""FastAPI app: board API, sources editor, settings, scrape trigger, daily schedule."""
import logging
import os
import threading
from contextlib import asynccontextmanager

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import bitwarden, claude, config, db, discovery, documents, scrape, settings, skills

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("plott")
STATIC = os.path.join(os.path.dirname(__file__), "static")
scheduler = BackgroundScheduler()


def _schedule():
    sch = config.profile().get("schedule", {})
    scheduler.remove_all_jobs()
    if sch.get("enabled", True):
        scheduler.add_job(scrape.run, CronTrigger(hour=sch.get("hour", 7), minute=sch.get("minute", 0)),
                          kwargs={"trigger": "scheduled"}, id="daily", misfire_grace_time=3600, coalesce=True)
        log.info("Daily scrape scheduled at %02d:%02d", sch.get("hour", 7), sch.get("minute", 0))


@asynccontextmanager
async def lifespan(app):
    bitwarden.load_secrets()
    mode = claude.auth_mode()
    log.info("Claude: %s", f"on ({mode})" if mode else "off (no credentials; scoring and tailoring are disabled)")
    db.conn()
    _schedule()
    scheduler.start()
    last = db.q("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    if last:
        import json
        scrape.state["last"] = {"started_at": last[0]["started_at"], "finished_at": last[0]["finished_at"],
                                **json.loads(last[0]["summary"] or "{}")}
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Plott", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_pages(request, call_next):
    """Make browsers check for a newer page, script, or stylesheet on every load (a cheap 304 when
    nothing changed). Without this they guess how long to cache, and after a deploy can pair the new
    page with an old app.js / style.css."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

LIST_COLS = ("id, source, company, title, url, location, workplace, employment_type, pay, pay_min, pay_max, "
             "category, posted_at, found_at, applied_at, status, status_changed_at, score, notes, "
             "documents IS NOT NULL AND documents LIKE '%_pdf%' AS has_docs")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


def _categories(p):
    cats = list(p.get("categories") or claude.DEFAULT_CATEGORIES)
    return cats if "Other" in cats else cats + ["Other"]


def _with_skills(p, jobs):
    """Each job's matches against the profile's skill tiers, worked out from its description on every
    load, so edits to the tiers show on every card right away."""
    tiers = skills.compile_skills(p)
    for j in jobs:
        text = f"{j.pop('_t') or ''}\n{j.pop('_d') or ''}"
        j["skills"] = skills.matches(tiers, text) if tiers else None
    return jobs


@app.get("/api/board")
def board():
    p = config.profile()
    scrape.expire_applied(p)   # also runs at the start of each scrape; here too in case scraping is off
    nxt = scheduler.get_job("daily")
    return {
        "profile": {"name": p.get("name", ""), "daily_goal": p.get("daily_goal", 10),
                    "no_reply_days": p.get("no_reply_days", 14),
                    "categories": _categories(p), "tag_categories": p.get("tag_categories") or []},
        "claude": claude.available(),
        "jobs": _with_skills(p, db.q(f"SELECT {LIST_COLS}, title AS _t, description AS _d FROM jobs "
                                     "ORDER BY COALESCE(status_changed_at, found_at) DESC")),
        "next_run": nxt.next_run_time.isoformat() if nxt and nxt.next_run_time else None,
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    rows = db.q("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not rows:
        raise HTTPException(404)
    return rows[0]


@app.post("/api/jobs")
def add_job(body: dict = Body(...)):
    if not body.get("company") or not body.get("title"):
        raise HTTPException(400, "company and title are required")
    status = body.get("status", "applied")
    if status not in db.STATUSES:
        raise HTTPException(400, "bad status")
    job = {k: body.get(k) for k in ("company", "title", "url", "location", "workplace", "pay", "category",
                                     "notes", "employment_type")}
    job.update(source="manual", external_id=body.get("url") or f"{job['company']}|{job['title']}|{db.now()}",
               status=status)
    if status in ("applied", "interviewing", "offer", "rejected", "no_response"):
        job["applied_at"] = body.get("applied_at") or db.now()
    job_id = db.add_job(job)
    if not job_id:
        raise HTTPException(409, "That job is already on the board")
    return {"id": job_id}


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: int, body: dict = Body(...)):
    try:
        db.update_job(job_id, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_job(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    db.delete_job(job_id)
    return {"ok": True}


@app.get("/api/sources")
def get_sources():
    with open(config.SOURCES_PATH) as f:
        return {"text": f.read()}


@app.put("/api/sources")
def put_sources(body: dict = Body(...)):
    text = body.get("text", "")
    try:
        parsed = yaml.safe_load(text) or {}
        if not isinstance(parsed.get("sources", []), list):
            raise ValueError("`sources` must be a list")
    except (yaml.YAMLError, ValueError) as e:
        raise HTTPException(400, f"Invalid YAML: {e}")
    with open(config.SOURCES_PATH, "w") as f:
        f.write(text)
    return {"ok": True, "count": len(parsed.get("sources") or [])}


@app.get("/api/settings")
def get_settings():
    return {"settings": settings.current()}


@app.put("/api/settings")
def put_setting(body: dict = Body(...)):
    try:
        value = settings.update(body.get("path", ""), body.get("value"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if str(body.get("path", "")).startswith("schedule."):
        _schedule()   # re-arm the daily scrape with the new time / on-off
    nxt = scheduler.get_job("daily")
    return {"ok": True, "value": value, "next_run": nxt.next_run_time.isoformat() if nxt and nxt.next_run_time else None}


@app.post("/api/scrape")
def start_scrape():
    if scrape.state["running"]:
        return {"started": False, "reason": "already running"}
    threading.Thread(target=scrape.run, kwargs={"trigger": "manual"}, daemon=True).start()
    return {"started": True}


@app.get("/api/scrape/status")
def scrape_status():
    return scrape.state


@app.get("/api/screened")
def screened(limit: int = 200):
    return db.q("SELECT * FROM seen ORDER BY at DESC LIMIT ?", (limit,))


@app.get("/api/companies")
def companies():
    from .sources.ats import configured_slugs
    listed = configured_slugs(config.sources())
    return [{**c, "configured": (c["ats"], c["slug"]) in listed} for c in discovery.all_companies()]


@app.patch("/api/companies/{ats}/{slug}")
def toggle_company(ats: str, slug: str, body: dict = Body(...)):
    discovery.set_enabled(ats, slug, bool(body.get("enabled")))
    return {"ok": True}


DOC_KINDS = {"resume_pdf", "resume_md", "cover_pdf", "cover_md"}


@app.post("/api/jobs/{job_id}/documents")
def make_documents(job_id: int, body: dict = Body(default={})):
    get_job(job_id)
    if not claude.available():
        raise HTTPException(400, claude.NOT_CONFIGURED)
    want_resume, want_cover = body.get("resume", True), body.get("cover_letter", True)
    if not (want_resume or want_cover):
        raise HTTPException(400, "Nothing requested")
    return {"started": documents.start(job_id, want_resume, want_cover)}


@app.get("/api/jobs/{job_id}/documents")
def document_status(job_id: int):
    docs = documents.files(job_id)
    return {**documents.status.get(job_id, {"state": "idle"}),
            "files": {k: os.path.basename(v) for k, v in docs.items() if k in DOC_KINDS},
            "changes": docs.get("changes", ""), "generated_at": docs.get("generated_at")}


@app.get("/api/jobs/{job_id}/documents/{kind}")
def document_file(job_id: int, kind: str):
    path = documents.files(job_id).get(kind) if kind in DOC_KINDS else None
    if not path or not os.path.exists(path):
        raise HTTPException(404)
    media = "application/pdf" if path.endswith(".pdf") else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, filename=os.path.basename(path),
                        content_disposition_type="inline")
