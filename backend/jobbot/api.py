"""Local API. Bound to 127.0.0.1 and closed to other websites by Host, Origin and header checks."""
import threading
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auto, config, costs, db, filters, limits, pipeline, review, scoring, walk
from .cv import parse
from .llm.client import LLMError
from .llm import client as llm
from .models import TRANSITIONS
from .sources import ats, discover

app = FastAPI(title="jobbot", docs_url=None, redoc_url=None, openapi_url=None)
HOSTS = {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"}


@app.middleware("http")
async def only_this_page(req: Request, nxt):
    if req.headers.get("host") not in HOSTS:
        return JSONResponse({"detail": "bad host"}, 403)
    origin = req.headers.get("origin")
    if origin and origin not in {f"http://{h}" for h in HOSTS}:
        return JSONResponse({"detail": "bad origin"}, 403)
    if req.url.path.startswith("/api") and req.method not in ("GET", "HEAD") and req.headers.get("x-jobbot") != "1":
        return JSONResponse({"detail": "missing header"}, 403)
    r = await nxt(req)
    r.headers["X-Frame-Options"] = "SAMEORIGIN"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    if req.url.path.startswith("/api"):
        r.headers["Cache-Control"] = "no-store"
    return r


for exc, code in ((pipeline.NotReady, 409), (db.BadTransition, 409), (LLMError, 502), (costs.LimitReached, 429), (RuntimeError, 409)):
    app.add_exception_handler(exc, lambda req, e, code=code: JSONResponse({"detail": str(e)[:300]}, code))

LIST = "id,company,title,locations,url,ats,source,family,status,band,filter_reason,deadline,posted_at,next_action,first_seen,updated_at,score,tier,triage"


def slim(d: dict) -> dict:
    s = d.pop("score", None)
    d["coverage"] = s.get("coverage") if s else None
    d["summary"] = s.get("summary") if s else None
    d["eligible"] = s.get("eligible") if s else None
    d["competition"] = s.get("competition") if s else None
    d["task"] = pipeline.tasks.get(d["id"])
    d["regions"] = filters.regions(d["locations"] or [])
    return d


@app.get("/api/jobs")
def jobs():
    with db.connect() as con:
        emp = limits.employers(con)
        return [{**slim(d := db.row(r)), "limit": limits.status(emp[limits.key(d["company"], d["id"])], d["id"])}
                for r in con.execute(f"SELECT {LIST} FROM jobs ORDER BY first_seen DESC")]


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    with db.connect() as con:
        j = db.get_job(con, job_id)
        if not j:
            raise HTTPException(404)
        a = db.get_application(con, job_id)
        ev = [dict(r) for r in con.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,))]
        cv = db.get_setting(con, "cv")
        e, cap = limits.of(con, j), db.settings(con)["max_per_company"]
    if a:
        a["approved"] = review.is_approved(a)
        a["blockers"] = len(review.blockers(a))
        a["has_pdf"] = bool(a["pdf_path"] and Path(a["pdf_path"]).exists())
        a.pop("pdf_path"), a.pop("approved_hash")
    return {**j, "application": a, "events": ev, "cv": cv, "task": pipeline.tasks.get(job_id), "walk": walk.state(job_id),
            "moves": sorted(TRANSITIONS[j["status"]]), "company_applications": [a for a in e["applied"] if a["id"] != job_id], "company_cap": cap, "limit": limits.status(e, job_id),
            "autofill": j["ats"] in ats.POLL}


@app.get("/api/limits")
def get_limits():
    with db.connect() as con:
        emp, cap = limits.employers(con), db.settings(con)["max_per_company"]
    sent = sorted((a for e in emp.values() for a in e["applied"]), key=lambda a: a["date"], reverse=True)
    return {"cap": cap, "applied": sent, "employers": sorted((e for e in emp.values() if e["limit"] or e["yours"] or e["applied"] or e["live"]), key=lambda e: e["company"].lower())}


class OwnLimit(BaseModel):
    n: int | None = Field(None, ge=0, le=50)        # 0: no limit. None: go back to what the adverts say.
    days: int = Field(limits.CYCLE, ge=1, le=1500)


@app.put("/api/limits/{k}")
def put_limit(k: str, b: OwnLimit):
    with db.connect() as con:
        if k not in limits.employers(con):
            raise HTTPException(404)
        mine = db.get_setting(con, "employer_limits", {})
        mine.pop(k, None)
        if b.n is not None:
            mine[k] = {"n": b.n, "days": b.days}
        db.put_setting(con, "employer_limits", mine)
    return {"ok": True}


class Outside(BaseModel):
    company: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    date: date


@app.post("/api/outside")
def add_outside(b: Outside):
    """An application you made without this dashboard, so counts and limits are right."""
    if not b.company.strip() or not b.title.strip() or b.date > date.today():
        raise HTTPException(422, "Give the employer, the role and a date that has passed.")
    with db.connect() as con:
        db.put_setting(con, "outside_apps", [*db.get_setting(con, "outside_apps", []), {"id": uuid4().hex, "company": b.company.strip(), "title": b.title.strip(), "date": b.date.isoformat()}])
    return {"ok": True}


@app.delete("/api/outside/{i}")
def drop_outside(i: str):
    with db.connect() as con:
        db.put_setting(con, "outside_apps", [o for o in db.get_setting(con, "outside_apps", []) if o["id"] != i])
    return {"ok": True}


class NewJob(BaseModel):
    url: str
    company: str
    title: str
    location: str = "United Kingdom"
    description: str = ""
    deadline: str | None = None


@app.post("/api/jobs")
def add(b: NewJob):
    if not b.url.startswith(("http://", "https://")) or not b.company.strip() or not b.title.strip():
        raise HTTPException(422, "A web address, company and title are needed.")
    return {"id": discover.add_url(b.url.strip(), b.company.strip(), b.title.strip(), b.location, b.description, b.deadline)}


class Patch(BaseModel):
    notes: str | None = None
    next_action: str | None = None
    description: str | None = None
    deadline: str | None = None


@app.patch("/api/jobs/{job_id}")
def patch(job_id: int, b: Patch):
    with db.connect() as con:
        for k, v in b.model_dump(exclude_none=True).items():
            con.execute(f"UPDATE jobs SET {k}=?, updated_at=? WHERE id=?", (v or None if k != "notes" else v, db.now(), job_id))
    return {"ok": True}


class Move(BaseModel):
    to: str
    detail: str | None = None


@app.post("/api/jobs/{job_id}/move")
def move(job_id: int, b: Move):
    pipeline.move(job_id, b.to, b.detail)
    return {"ok": True}


class Ids(BaseModel):
    ids: list[int]


@app.post("/api/score")
def score(b: Ids):
    pipeline.candidate()
    n = 0
    for i in b.ids[:100]:
        try:
            pipeline.start(i, "score", pipeline.score)
            n += 1
        except pipeline.NotReady:
            pass
    return {"started": n}


class AutoMode(BaseModel):
    mode: Literal["rate", "tailor"]


@app.get("/api/auto")
def auto_state():
    s = {k: v for k, v in auto.state.items() if not k.startswith("_")}
    if s["job"]:
        s["job"] = {**s["job"], "step": (pipeline.tasks.get(s["job"]["id"]) or {}).get("step")}
    return {**s, **auto.summary()}


@app.post("/api/auto")
def auto_start(b: AutoMode):
    auto.start(b.mode)
    return {"ok": True}


@app.post("/api/auto/stop")
def auto_stop():
    auto.stop()
    return {"ok": True}


class Tier(BaseModel):
    tier: Literal[1, 2, 3, 4]


@app.put("/api/jobs/{job_id}/tier")
def set_tier(job_id: int, b: Tier):
    auto.set_tier(job_id, b.tier)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/tailor")
def tailor(job_id: int):
    pipeline.candidate()
    pipeline.start(job_id, "tailor", pipeline.tailor_job)
    return {"ok": True}


class Edit(BaseModel):
    cv: dict[str, Any] | None = None
    cover_letter: str | None = None
    answers: dict[str, list[str]] | None = None


@app.put("/api/jobs/{job_id}/application")
def edit(job_id: int, b: Edit):
    if pipeline.tasks.get(job_id, {}).get("running"):
        raise HTTPException(409, "Wait for the current step to finish.")
    pipeline.recheck(job_id, **b.model_dump())
    return {"ok": True}


class LetterRequest(BaseModel):
    notes: str = ""
    fresh: bool = False


@app.post("/api/jobs/{job_id}/letter")
def letter(job_id: int, b: LetterRequest):
    pipeline.candidate()
    pipeline.start(job_id, "letter", lambda i: pipeline.letter_job(i, b.notes[:2000], b.fresh))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/rereview")
def rereview(job_id: int):
    pipeline.start(job_id, "review", pipeline.rereview)
    return {"ok": True}


class Approve(BaseModel):
    override: str | None = None


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: int, b: Approve):
    pipeline.approve(job_id, b.override)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/open")
def open_form(job_id: int):
    pipeline.open_form(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/fill")
def fill(job_id: int):
    if (walk.state(job_id) or {}).get("running"):
        raise pipeline.NotReady("This application is being filled already. Stop that first.")
    return pipeline.fill_page(job_id)


class Link(BaseModel):
    url: str


@app.put("/api/jobs/{job_id}/link")
def link(job_id: int, b: Link):
    return walk.set_link(job_id, b.url)


@app.post("/api/jobs/{job_id}/fill-all")
def fill_all(job_id: int):
    walk.start(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/fill-stop")
def fill_stop(job_id: int):
    walk.stop(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/cv.pdf")
def pdf(job_id: int):
    with db.connect() as con:
        a = db.get_application(con, job_id)
    if not a or not a["pdf_path"] or not Path(a["pdf_path"]).exists():
        raise HTTPException(404)
    return FileResponse(a["pdf_path"], media_type="application/pdf", content_disposition_type="inline")


@app.post("/api/discover")
def run_discovery():
    if discover.state["running"]:
        raise HTTPException(409, "Discovery is already running.")
    threading.Thread(target=discover.run, daemon=True).start()
    return {"ok": True}


@app.post("/api/discover/web")
def run_web_search():
    if discover.state["running"]:
        raise HTTPException(409, "Discovery is already running.")
    if not llm.status()["ok"]:
        raise HTTPException(409, llm.status()["detail"])
    with db.connect() as con:
        costs.check(con)

    def go():
        try:
            discover.run_web()
        except Exception as e:     # known problems are already in state["errors"]
            if not discover.state["errors"]:
                discover.state["errors"].append(f"{type(e).__name__}: {str(e)[:150]}")
    threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


@app.get("/api/status")
def status():
    with db.connect() as con:
        s = db.settings(con)
        return {"discover": discover.state, "claude": llm.status(), "calls_today": costs.calls_today(con), "daily_calls": s["daily_calls"], "plan": costs.plan(con), "usage": costs.usage(con), "stop_percent": s["plan_stop_percent"],
                "cv": db.get_setting(con, "cv") is not None, "cv_confirmed": db.get_setting(con, "cv_confirmed", False),
                "web_cap": s["web_search_calls"], "auto": {k: auto.state[k] for k in ("running", "phase", "done", "total", "waiting")}, "running": sum(1 for t in pipeline.tasks.values() if t["running"]), "model": s["model"]}


@app.get("/api/settings")
def get_settings():
    with db.connect() as con:
        return {**db.settings(con), "cv": db.get_setting(con, "cv"), "cv_confirmed": db.get_setting(con, "cv_confirmed", False),
                "transcript": any(config.DOCS.glob("transcript*.pdf"))}


@app.put("/api/settings")
def put_settings(b: dict[str, Any]):
    unknown = set(b) - set(config.DEFAULT_SETTINGS)
    if unknown:
        raise HTTPException(422, f"Unknown settings: {sorted(unknown)}")
    for k, lo, hi in (("daily_calls", 0, 2000), ("plan_stop_percent", 5, 100), ("web_search_calls", 2, 100), ("max_per_company", 1, 20), ("reuse_percent", 0, 100)):
        if k in b and not lo <= float(b[k]) <= hi:
            raise HTTPException(422, f"{k.replace('_', ' ')} must be between {lo} and {hi}.")
    for p in (b.get("priors") or {}).values():
        if not 0 < p["offer"] <= p["interview"] < 1:
            raise HTTPException(422, "Each offer rate must be above 0 and no higher than the interview rate.")
    if "regions" in b and (not b["regions"] or set(b["regions"]) - set(filters.REGIONS)):
        raise HTTPException(422, "Choose at least one country.")
    with db.connect() as con:
        if set(b.get("regions", [])) - set(db.settings(con)["regions"]):
            # Postings for a newly added country were skipped before, so every board needs reading again.
            con.execute("UPDATE boards SET polled_at=NULL")
        for k, v in b.items():
            db.put_setting(con, k, v)
    if "families" in b or "regions" in b:
        discover.reclassify()
    return {"ok": True}


async def _save_pdf(f: UploadFile, name: str) -> Path:
    data = await f.read(10_000_001)
    if len(data) > 10_000_000 or not data.startswith(b"%PDF"):
        raise HTTPException(422, "Upload a PDF under 10 MB.")
    config.ensure_dirs()
    path = config.DOCS / name
    path.write_bytes(data)
    return path


@app.post("/api/cv/upload")
async def upload_cv(file: UploadFile):
    from starlette.concurrency import run_in_threadpool
    path = await _save_pdf(file, "master_cv.pdf")
    cv = await run_in_threadpool(parse.parse_pdf, path)
    with db.connect() as con:
        db.put_setting(con, "cv", cv)
        db.put_setting(con, "cv_confirmed", False)
    return cv


@app.post("/api/transcript")
async def upload_transcript(file: UploadFile):
    await _save_pdf(file, "transcript.pdf")
    return {"ok": True}


class CV(BaseModel):
    cv: dict[str, Any]
    confirmed: bool


@app.put("/api/cv")
def save_cv(b: CV):
    try:
        cv = {"name": str(b.cv["name"]), "email": str(b.cv.get("email", "")), "phone": str(b.cv.get("phone", "")), "links": b.cv.get("links", []),
              "entries": [{k: str(e.get(k, "")) for k in ("kind", "title", "organisation", "location", "dates")} |
                          {"bullets": e["bullets"]} for e in b.cv["entries"]],
              "skills": [{"group": str(s["group"]), "items": [str(i) for i in s["items"] if str(i).strip()]} for s in b.cv["skills"]]}
    except (KeyError, TypeError):
        raise HTTPException(422, "The CV data is incomplete.")
    with db.connect() as con:
        db.put_setting(con, "cv", parse.with_ids(cv))
        db.put_setting(con, "cv_confirmed", b.confirmed)
    return {"ok": True}


@app.get("/api/insights")
def insights():
    with db.connect() as con:
        lk, out = scoring.likelihoods(con), scoring.outcomes(con)
        reached: dict[str, set] = {}
        for r in con.execute("SELECT job_id,to_status FROM events WHERE to_status IS NOT NULL"):
            reached.setdefault(r["to_status"], set()).add(r["job_id"])
        funnel = [{"stage": s, "n": len(reached.get(s, ()))} for s in
                  ("scored", "shortlisted", "approved", "submitted", "assessment", "interview", "offer")]
        fam = {}
        for o in out:
            f = fam.setdefault(o["family"], {"family": o["family"], "submitted": 0, "resolved": 0, "interviews": 0, "offers": 0})
            f["submitted"] += 1
            f["resolved"] += o["resolved"]
            f["interviews"] += o["interview"]
            f["offers"] += o["offer"]
        calls = [dict(r) for r in con.execute("SELECT purpose, COUNT(*) calls FROM usage GROUP BY purpose ORDER BY calls DESC")]
        src = [dict(r) for r in con.execute("SELECT source, COUNT(*) n FROM jobs WHERE status!='filtered_out' GROUP BY source ORDER BY n DESC")]
        boards = dict(con.execute("SELECT COUNT(*) total, COALESCE(SUM(ok),0) ok FROM boards").fetchone())
    return {"likelihood": lk, "funnel": funnel, "families": list(fam.values()), "calls": calls, "sources": src, "boards": boards}


if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(config.FRONTEND_DIST / "index.html")
