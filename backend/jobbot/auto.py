"""Rates every open role 1 to 4, then shortlists and tailors tiers 1, 2 and 3 in that order.

It stops at "needs review": approving, filling and submitting stay with the user. When a usage limit is near it waits and
carries on by itself.
"""
import re
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import costs, db, limits, pipeline, scoring
from .cv import reuse
from .cv.parse import context
from .llm import client, prompts
from .llm.client import LLMError
from .llm.schemas import Ratings
from .models import APPLIED, Status

S = Status
BATCH, BARE_BATCH, TEXT, HOLD = 12, 40, 3500, 30
PHD_ONLY = re.compile(r"\bph\.?\s?d\b|\bdoctoral\b|\bpost-?doc", re.I)
OTHER_LEVEL = re.compile(r"undergrad|bachelor|master|\bb\.?sc?\b|\bm\.?sc?\b|\bmeng\b|\bbs\b|\bms\b", re.I)
NEED = {"calls": 10, "percent": 6}      # room to finish one application before starting it
BUSY = {str(s) for s in APPLIED} | {"tailoring", "needs_review", "approved", "filling"}
READY = "ready to review"
DONE = BUSY - {"tailoring"}
USEFUL = re.compile(r"require|qualif|must|degree|ph\.?d|student|graduat|experience|skill|eligib|visa|sponsor|right to work|start|week|month|"
                    r"duration|deadline|you will|you have|you are|year|bachelor|master|20\d\d", re.I)
_stop = threading.Event()
_lock = threading.Lock()


def blank() -> dict:
    return {"running": False, "mode": None, "phase": "idle", "done": 0, "total": 0, "job": None, "waiting": None, "stopping": False,
            "errors": [], "finished": [], "message": None}


state = blank()


# ---- rating ----
def shorten(text: str) -> str:
    """Requirements usually come late in an advert, so keep the start and then the lines that matter."""
    if len(text) <= TEXT:
        return text
    out, n = text[:1000], 1000
    for line in re.split(r"\n+|(?<=[.;])\s+", text[1000:]):
        if USEFUL.search(line) and n + len(line) < TEXT:
            out += "\n" + line
            n += len(line) + 1
    return out


def readable(j: dict) -> bool:
    return len(j["description"] or "") > 200


def as_score(r) -> dict:
    """The quick counts in the same form as a full score, so both use the same bands."""
    reqs = [{"essential": e, "met": m} for e, m, n in (
        (True, "yes", r.must_met), (True, "partial", r.must_partial), (True, "no", r.must_missing), (True, "unknown", r.must_unknown),
        (False, "yes", r.nice_met), (False, "no", r.nice_missing)) for _ in range(max(0, min(n, 12)))]
    return {"is_relevant_internship": r.is_relevant_internship, "eligible": r.eligible, "dates": r.dates, "competition": r.competition, "requirements": reqs}


def unrated(con) -> list[dict]:
    return [db.row(r) for r in con.execute("SELECT * FROM jobs WHERE tier IS NULL AND status IN ('discovered','scored') ORDER BY id")]


def save(con, j: dict, tier: int, reason: str, basis: str, **extra) -> None:
    con.execute("UPDATE jobs SET tier=?, triage=? WHERE id=?", (tier, db.json.dumps({"reason": reason, "basis": basis, **extra}), j["id"]))
    r = con.execute("SELECT status FROM jobs WHERE id=?", (j["id"],)).fetchone()
    if tier == 4 and r["status"] in ("discovered", "scored"):
        con.execute("UPDATE jobs SET filter_reason=? WHERE id=?", (reason, j["id"]))
        db.set_status(con, j["id"], S.filtered_out, "rated 4: " + reason)


def rate_batch(jobs: list[dict], cv: dict, profile: dict) -> int:
    ids = {f"j{n}": j for n, j in enumerate(jobs, 1)}
    body = [{"id": i, "company": j["company"], "title": j["title"], "locations": j["locations"],
             "advert": shorten(j["description"]) if readable(j) else "(not available)"} for i, j in ids.items()]
    out = client.call("rate", prompts.RATE, context(cv, profile), client.tag("job", body) + f"\n\nToday is {date.today().isoformat()}. Rate each role.",
                      Ratings, effort="low")
    seen = set()
    with db.connect() as con:
        for r in out.ratings:
            j = ids.get(r.id)
            if not j or r.id in seen:
                continue
            seen.add(r.id)
            now = con.execute("SELECT tier,status FROM jobs WHERE id=?", (j["id"],)).fetchone()
            if now["tier"] is not None or now["status"] not in ("discovered", "scored"):
                continue        # the user acted on it meanwhile
            s = as_score(r)
            t = scoring.tier(s, scoring.band(s, j["posted_at"]))
            if j["score"]:      # already scored in full; the quick pass may only lower it
                t = max(t, scoring.tier(j["score"], j["band"]))
            basis = "quick"
            if not readable(j):
                t, basis = max(t, 3), "title"
            if re.fullmatch(r"20\d\d-\d\d-\d\d", r.deadline or "") and readable(j):
                con.execute("UPDATE jobs SET deadline=COALESCE(deadline,?) WHERE id=?", (r.deadline, j["id"]))
                if r.deadline < date.today().isoformat():
                    t = 4
            save(con, j, t, r.reason.strip()[:200], basis, coverage=scoring.coverage(s))
    return len(seen)


def rate_all(cv: dict, profile: dict) -> None:
    today = date.today().isoformat()
    with db.connect() as con:
        for j in unrated(con):
            if j["deadline"] and j["deadline"][:10] < today:
                save(con, j, 4, "The deadline has passed.", "code")
        if not re.search(r"ph\.?\s?d|doctor", profile["degree_level"] or "", re.I):
            for j in unrated(con):
                if PHD_ONLY.search(j["title"]) and not OTHER_LEVEL.search(j["title"]):
                    save(con, j, 4, "The title asks for a PhD student.", "code")
        todo = unrated(con)
    missing = [j for j in todo if not readable(j)]
    state.update(phase="reading adverts", done=0, total=len(missing))

    def fetch(j):
        if not _stop.is_set():
            try:
                pipeline.ensure_description(j)
            except Exception:
                pass        # rated from its title instead
        state["done"] += 1
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(fetch, missing))
    for attempt in range(2):        # anything left out of a reply gets one more go
        with db.connect() as con:
            todo = unrated(con)
        if not todo or _stop.is_set():
            return
        state.update(phase="rating", done=0, total=len(todo))
        full, bare = [j for j in todo if readable(j)], [j for j in todo if not readable(j)]
        batches = [full[n:n + BATCH] for n in range(0, len(full), BATCH)] + [bare[n:n + BARE_BATCH] for n in range(0, len(bare), BARE_BATCH)]
        for batch in batches:
            if not wait_for_room(2, 2):
                return
            try:
                rate_batch(batch, cv, profile)
                state["_fails"] = 0
            except costs.LimitReached:
                continue        # picked up on the second pass
            except LLMError as e:
                failed(str(e))
                if state["_fails"] >= 3:
                    raise
            state["done"] += len(batch)


# ---- shortlisting and tailoring ----
def candidates(con) -> tuple[list[dict], int, int, list[dict]]:
    """(queue in order, roles held back by the company cap, roles with no advert text, second listings of a role).
    Shortlisting a second listing yourself lets it through."""
    cap, today = db.settings(con)["max_per_company"], date.today().isoformat()
    rows = [db.row(r) for r in con.execute("""SELECT id,company,title,locations,description,status,tier,triage,score,deadline,length(description) n FROM jobs
        WHERE tier IN (1,2,3) AND status IN ('discovered','scored','shortlisted')""")]
    emp = limits.employers(con)
    sent = {a["id"] for e in emp.values() for a in e["applied"]}
    # Yours: everything sent or under way. Theirs: what still counts towards the employer's own limit.
    busy = Counter(limits.key(r["company"], r["id"]) for r in con.execute("SELECT id,company,status FROM jobs") if r["status"] in BUSY and r["status"] != "withdrawn" and r["id"] not in sent)
    used = Counter({k: len(e["applied"]) + busy[k] for k, e in emp.items()})
    room = {k: min(cap - used[k], e["left"] - busy[k] if e["limit"] else cap) for k, e in emp.items()}
    cov = lambda r: (r["score"] or {}).get("coverage") or (r["triage"] or {}).get("coverage") or 0
    rows.sort(key=lambda r: (r["tier"], r["deadline"] or "9999", -cov(r)))
    queue, held, bare, twins = [], 0, 0, []
    taken = {}      # roles already tailored or ahead in the queue, by title
    for j in con.execute("SELECT id,company,title,locations,description,status FROM jobs"):
        if j["status"] in BUSY:
            taken.setdefault(db.norm(j["title"]), []).append(db.row(j))
    for r in rows:
        k, title = limits.key(r["company"], r["id"]), db.norm(r["title"])
        if r["deadline"] and r["deadline"][:10] < today:
            continue
        if (r["n"] or 0) <= 200:
            bare += 1
        elif r["status"] != "shortlisted" and (twin := next((x for x in taken.get(title, []) if reuse.same_listing(r, x)), None)):
            twins.append({"id": r["id"], "company": r["company"], "title": r["title"], "twin": twin["company"]})
        elif room[k] <= 0:
            held += 1
        else:
            room[k] -= 1
            queue.append(r)
            taken.setdefault(title, []).append(r)
    return queue, held, bare, twins


def summary() -> dict:
    with db.connect() as con:
        queue, held, bare, twins = candidates(con)
        rows = con.execute("SELECT tier,status,COUNT(*) n FROM jobs WHERE tier IS NOT NULL GROUP BY tier,status").fetchall()
        left = con.execute("SELECT COUNT(*) n FROM jobs WHERE tier IS NULL AND status IN ('discovered','scored')").fetchone()["n"]
        usage = [dict(r) for r in con.execute("SELECT job_id, COUNT(*) calls FROM usage WHERE purpose IN ('tailor','cover','answers','review','company_search','company_research','score') AND job_id IS NOT NULL GROUP BY job_id HAVING SUM(purpose='tailor')>0")]
    tiers = []
    for t in (1, 2, 3, 4):
        rs = [r for r in rows if r["tier"] == t]
        tiers.append({"tier": t, "total": sum(r["n"] for r in rs if r["status"] not in ("closed", "withdrawn")),
                      "tailored": sum(r["n"] for r in rs if r["status"] in DONE), "queued": sum(q["tier"] == t for q in queue)})
    return {"tiers": tiers, "unrated": left, "held": held, "bare": bare, "twins": twins, "next": [{k: q[k] for k in ("id", "company", "title", "tier")} for q in queue if q["id"] != (state.get("job") or {}).get("id")][:5],
            "calls_each": round(sum(u["calls"] for u in usage) / len(usage), 1) if usage else None}


def next_job(skip: set[int]) -> dict | None:
    with db.connect() as con:
        return next((j for j in candidates(con)[0] if j["id"] not in skip), None)


def one(j: dict) -> str:
    """Score it properly first if that has not happened: the quick rating is not worth a whole application."""
    i = j["id"]
    if j["status"] != "shortlisted" and (not j["score"] or "dates" not in j["score"]):
        pipeline.run(i, "score", pipeline.score)
        with db.connect() as con:
            now = db.get_job(con, i)
        if now["tier"] != j["tier"] or now["status"] == "filtered_out":
            return f"moved to tier {now['tier']} after a full score"
        if now["deadline"] and now["deadline"] < date.today().isoformat():
            raise pipeline.NotReady(f"The full advert says it closed on {now['deadline']}, so it was not tailored.")
    with db.connect() as con:
        if db.get_job(con, i)["status"] in ("discovered", "scored"):
            db.set_status(con, i, S.shortlisted, f"automatic, tier {j['tier']}")
    pipeline.run(i, "tailor", pipeline.tailor_job)
    with db.connect() as con:
        f = (db.get_application(con, i) or {}).get("cv_from") or {}
    return READY + ("" if not f.get("job_id") else f", CV adapted from {f['company']}" if f.get("changed") else f", CV reused from {f['company']}")


def tailor_all() -> None:
    skip: set[int] = set()
    while not _stop.is_set():
        j = next_job(skip)
        if not j:
            return
        if not wait_for_room(**NEED):
            return
        with db.connect() as con:
            queue = candidates(con)[0]
        state.update(phase="tailoring", total=len(queue) + state["done"])
        state["job"] = {"id": j["id"], "company": j["company"], "title": j["title"], "tier": j["tier"], "since": time.time()}
        t, result, ok = time.time(), "", True
        try:
            result = one(j)
            state["_fails"] = 0
            if result.startswith(READY):
                state["done"] += 1
        except costs.LimitReached:
            continue        # waits at the top, then tries this role again
        except pipeline.NotReady as e:
            result, ok = str(e), False
            skip.add(j["id"])
        except Exception as e:
            if not isinstance(e, LLMError):
                traceback.print_exc()
            result, ok = str(e)[:200] if isinstance(e, LLMError) else f"{type(e).__name__}: {str(e)[:150]}", False
            if not failed(result):      # a plan limit is not the role's fault
                skip.add(j["id"])
            if state["_fails"] >= 3:
                raise
        finally:
            state["job"] = None
        state["finished"] = [{**{k: j[k] for k in ("id", "company", "title", "tier")}, "result": result, "ok": ok, "seconds": round(time.time() - t)}] + state["finished"][:59]


# ---- running ----
def failed(msg: str) -> bool:
    """Notes a failure. Returns True, after a pause, if it was the plan's own limit."""
    if re.search(r"usage limit|included usage has run out", msg):
        state["waiting"] = msg
        _stop.wait(900)
        state["waiting"] = None
        return True
    state["_fails"] = state.get("_fails", 0) + 1
    state["errors"] = ([msg] + state["errors"])[:5]
    return False


def wait_for_room(calls: int, percent: int) -> bool:
    """Waits while a limit is near. False if told to stop."""
    while not _stop.is_set():
        try:
            with db.connect() as con:
                costs.check(con, calls, percent)
            state["waiting"] = None
            return True
        except costs.LimitReached as e:
            state["waiting"] = str(e)
            _stop.wait(HOLD)
    state["waiting"] = None
    return False


def start(mode: str) -> None:
    cv, profile = pipeline.candidate()
    if not client.status()["ok"]:
        raise pipeline.NotReady(client.status()["detail"])
    with _lock:
        if state["running"]:
            raise pipeline.NotReady("It is already running.")
        _stop.clear()
        state.clear()
        state.update(blank(), running=True, mode=mode, _fails=0)
    with db.connect() as con:
        db.put_setting(con, "auto_mode", mode)

    def go():
        try:
            rate_all(cv, profile)
            if mode == "tailor":
                state.update(done=0)
                tailor_all()
            state["message"] = "Stopped." if _stop.is_set() else "Finished. Nothing is left in the queue." if mode == "tailor" else "All roles are rated."
        except Exception as e:
            if not isinstance(e, (LLMError, pipeline.NotReady)):
                traceback.print_exc()
            state["message"] = f"Stopped after repeated problems: {str(e)[:200]}"
        finally:
            with db.connect() as con:
                db.put_setting(con, "auto_mode", None)
            state.update(running=False, phase="idle", job=None, waiting=None, stopping=False)
    threading.Thread(target=go, daemon=True).start()


def stop() -> None:
    if state["running"]:
        state["stopping"] = True
        _stop.set()


def resume() -> None:
    """After a restart, carry on with what was running."""
    with db.connect() as con:
        mode = db.get_setting(con, "auto_mode")
    if mode:
        try:
            start(mode)
        except pipeline.NotReady:
            pass


def set_tier(job_id: int, tier: int) -> None:
    """The user's own rating, which nothing automatic changes."""
    with db.connect() as con:
        j = db.get_job(con, job_id)
        if not j:
            raise pipeline.NotReady("Job not found.")
        if tier < 4 and j["status"] == "filtered_out":
            con.execute("UPDATE jobs SET filter_reason=NULL WHERE id=?", (job_id,))
            db.set_status(con, job_id, S.discovered, f"you rated it {tier}")
        save(con, j, tier, "You chose this rating.", "you")
