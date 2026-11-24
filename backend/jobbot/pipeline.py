"""Moves a job through scoring, tailoring, review, approval and filling."""
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import config, costs, db, research, review, scoring
from .apply import fields
from .cv import render, reuse, tailor, verify
from .llm.client import LLMError
from .models import APPLIED, Status
from .sources import ats, pages
from .sources.http import client

MAX_REVISIONS = 2
pool = ThreadPoolExecutor(2)
tasks: dict[int, dict] = {}
_lock = threading.Lock()
S = Status


class NotReady(RuntimeError):
    """Something the user needs to do first. The message is shown as is."""


def recover() -> None:
    """After a restart nothing can still be in progress."""
    with db.connect() as con:
        for r in con.execute("SELECT id,status FROM jobs WHERE status IN ('tailoring','filling')").fetchall():
            db.set_status(con, r["id"], S.shortlisted if r["status"] == "tailoring" else S.approved, "restarted")


def claim(job_id: int, kind: str) -> None:
    with _lock:
        if tasks.get(job_id, {}).get("running"):
            raise NotReady("This job is already being worked on.")
        tasks[job_id] = {"kind": kind, "running": True, "error": None, "step": None}


def step(job_id: int, text: str) -> None:
    if job_id in tasks:
        tasks[job_id]["step"] = text


def run(job_id: int, kind: str, fn) -> None:
    """In this thread, so the caller sees what went wrong."""
    claim(job_id, kind)
    err = None
    try:
        fn(job_id)
    except Exception as e:
        err = str(e)[:200]
        raise
    finally:
        tasks[job_id] = {"kind": kind, "running": False, "error": err}


def start(job_id: int, kind: str, fn) -> None:
    claim(job_id, kind)

    def go():
        err = None
        try:
            fn(job_id)
        except (NotReady, LLMError, costs.LimitReached) as e:
            err = str(e)
        except Exception as e:
            traceback.print_exc()
            err = f"{type(e).__name__}: {str(e)[:200]}"
        tasks[job_id] = {"kind": kind, "running": False, "error": err}
    pool.submit(go)


def candidate() -> tuple[dict, dict]:
    with db.connect() as con:
        cv, ok, profile = db.get_setting(con, "cv"), db.get_setting(con, "cv_confirmed", False), db.settings(con)["profile"]
    if not cv or not ok:
        raise NotReady("Upload your CV and confirm the extracted facts on the Profile page first.")
    return cv, profile


def _job(job_id) -> dict:
    with db.connect() as con:
        j = db.get_job(con, job_id)
    if not j:
        raise NotReady("Job not found.")
    return j


def ensure_description(job: dict) -> dict:
    if len(job["description"] or "") > 200:
        return job
    text, deadline = "", None
    with client() as c:
        def page():
            nonlocal deadline
            p = pages.fetch(c, job["url"])
            deadline = p.get("deadline")
            return p.get("description", "")
        for get in (lambda: ats.fetch_description(c, job["ats"], job["url"], job["apply_url"]),
                    lambda: pages.vendor_api(c, job["url"]), page, lambda: pages.rendered(c, job["url"])):
            try:
                text = get() or ""
            except Exception:
                continue     # if every reader fails, the user is asked to paste it
            if len(text) >= 200:
                break
    if len(text) < 200:
        if job["description"]:
            return job
        raise NotReady("No description could be read. Open the posting and paste its text into this job.")
    with db.connect() as con:
        con.execute("UPDATE jobs SET description=?, deadline=COALESCE(deadline,?) WHERE id=?", (text, deadline, job["id"]))
    return _job(job["id"])


def score(job_id: int) -> None:
    cv, profile = candidate()
    job = ensure_description(_job(job_id))
    step(job_id, "Scoring against the advert")
    s = scoring.score_job(job, cv, profile)
    s["coverage"] = scoring.coverage(s)
    b = scoring.band(s, job["posted_at"])
    dl = s["deadline"]
    try:
        dl = datetime.strptime(dl or "", "%Y-%m-%d").date().isoformat()
    except ValueError:
        dl = None
    with db.connect() as con:
        db.save_score(con, job_id, s, b, s["family"] if s["family"] != "other" else job["family"], dl)
        # Your own rating stands, whatever the score says.
        yours = (job["triage"] or {}).get("basis") == "you"
        why = scoring.out_of_scope(s)
        if why and why != "not a relevant internship":
            why += ": " + "; ".join(s["eligibility_notes"])[:200]
        if not yours:
            con.execute("UPDATE jobs SET tier=?, triage=? WHERE id=?", (scoring.tier(s, b), db.json.dumps({"reason": why or s["summary"], "basis": "full", "coverage": s["coverage"]}), job_id))
        if job["status"] in (S.discovered, S.scored):
            bad = why and not yours
            if bad:
                con.execute("UPDATE jobs SET filter_reason=? WHERE id=?", (why, job_id))
            db.set_status(con, job_id, S.filtered_out if bad else S.scored, f"band {b}")


def company_count(con, job: dict) -> int:
    mine = db.norm(job["company"])
    live = {str(s) for s in APPLIED} | {"approved", "filling"}
    return sum(1 for r in con.execute("SELECT id,company,status FROM jobs") if
               r["id"] != job["id"] and db.norm(r["company"]) == mine and r["status"] in live)


def in_letter(i: dict) -> bool:
    return i["where"].lower().startswith("cover")


def weak(i: dict) -> bool:
    return i["severity"] == "warn" and i.get("by") == "AI"


def find_out(job: dict, fresh: bool = False) -> tuple[list[dict], str | None]:
    """Company research, and what to tell the user if there is none. A letter can still be written without it."""
    try:
        found = research.gather(job, fresh)
    except LLMError as e:
        return [], f"Company research did not finish ({e}), so the letter uses only the posting."
    return found, None if found else "No usable company pages could be read, so the letter uses only the posting."


def write_letter(job, cv, profile, found, notes, prev=None, issues=None) -> tuple[str, list[dict]]:
    text, used = tailor.cover_letter(job, cv, profile, found, notes, prev, issues)
    return text, [{**f, "used": f["id"] in used} for f in found]


def letter_job(job_id: int, notes: str | None = None, fresh: bool = False) -> None:
    """Research the company and write the letter again, leaving the CV and answers alone. Needs approving afterwards."""
    cv, profile = candidate()
    job = _job(job_id)
    with db.connect() as con:
        app = db.get_application(con, job_id)
    if job["status"] not in (S.needs_review, S.approved, S.filling) or not app or not app["cv"]:
        raise NotReady("Tailor the application first.")
    old, n = app["cover_letter"], (app["review"] or {}).get("pages")
    app["letter_notes"] = (app["letter_notes"] or "") if notes is None else notes.strip()
    found, problem = find_out(job, fresh)
    prev = issues = None
    polished = False
    for attempt in range(MAX_REVISIONS + 1):
        app["cover_letter"], app["research"] = write_letter(job, cv, profile, found, app["letter_notes"], prev, issues)
        checks = review.code_checks(job, cv, profile, app, n)
        stuck = any(i["severity"] == "block" and in_letter(i) for i in checks)
        if stuck and attempt < MAX_REVISIONS:
            prev, issues = app["cover_letter"], [i for i in checks if in_letter(i)]
            continue
        ai = review.ai_review(job, cv, profile, app)
        # A weak letter is rewritten once; an unsupported one as often as allowed.
        issues = [i for i in checks + ai["issues"] if in_letter(i) and (i["severity"] == "block" or not polished and weak(i))]
        polished = True
        if not issues or attempt == MAX_REVISIONS:
            break
        prev = app["cover_letter"]
    form = [i for i in (app["review"] or {}).get("issues", []) if i["where"] == "Form"]
    note = [{"severity": "note", "where": "Company research", "problem": problem, "suggestion": "", "by": "code"}] if problem else []
    rv = {**ai, "issues": checks + ai["issues"] + form + note, "pages": n, "revisions": attempt, "ai_stale": False}
    with db.connect() as con:
        # The form may have been opened while this ran, so the old letter must not stay approved.
        db.save_application(con, job_id, cover_letter=app["cover_letter"], research=app["research"], letter_notes=app["letter_notes"],
                            letter_prev=old or None, review=rv, approved_hash=None)
        if db.get_job(con, job_id)["status"] in (S.approved, S.filling):
            db.set_status(con, job_id, S.needs_review, "letter rewritten")
        db.log(con, job_id, "letter", detail=f"{len(found)} passages found, {sum(f['used'] for f in app['research'])} used")


def tailor_job(job_id: int) -> None:
    cv, profile = candidate()
    step(job_id, "Reading the advert")
    job = ensure_description(_job(job_id))
    with db.connect() as con:
        db.set_status(con, job_id, S.tailoring)
        old = db.get_application(con, job_id) or {}
        # Any new version must be approved again.
        db.save_application(con, job_id, approved_hash=None)
    try:
        form, known, ask, form_note = [], {}, [], None
        step(job_id, "Reading the application form")
        if job["ats"] in ats.POLL:
            try:
                from .apply.session import read_form
                form = read_form(job["apply_url"])
                if not fields.looks_like_application(form):
                    form = []
                    raise NotReady("no application form on that page")
                known, ask = fields.plan(form, profile)
            except Exception:
                form_note = "The form could not be read in advance; its questions will appear when you open it and press Fill this page."
        else:
            form_note = "This site needs you to sign in. Open it, then press Fill this page on each step."
        step(job_id, "Comparing with CVs already written")
        # Asking for a role to be tailored again means a new CV.
        with db.connect() as con:
            base = None if old.get("cv") else reuse.find(con, job, cv)
        cv_from = {"rev": reuse.rev(cv)}
        if base:
            t = {**base["cv"], "changes": [f"Same CV as for {base['company']}, {base['title']}."]}
            cv_from |= {k: base[k] for k in ("job_id", "company", "title", "match")}
        else:
            step(job_id, "Tailoring your CV")
            t = tailor.tailor(job, cv, profile)
        want_cover = bool(old.get("cover_letter")) or any(f["kind"] == "file" and "cover" in f["label"].lower() for f in form)
        notes, found, problem, cover = old.get("letter_notes") or "", [], None, ""
        if want_cover:
            step(job_id, "Researching the company")
            found, problem = find_out(job)
            step(job_id, "Writing the cover letter")
            cover, found = write_letter(job, cv, profile, found, notes)
        step(job_id, "Drafting form answers")
        drafted = tailor.draft_answers(job, cv, profile, ask)
        polished, dropped = False, []
        for attempt in range(MAX_REVISIONS + 1):
            answers = {**known, **fields.merge(ask, drafted)}
            step(job_id, "Checking and reviewing" if attempt == 0 else f"Reviewing again after fixes ({attempt})")
            t, path, n, cut = render.fit(t, cv, profile, job_id)
            dropped += cut
            app = {"cv": t, "cover_letter": cover, "answers": answers, "pdf_path": str(path),
                   "research": found, "letter_notes": notes}
            issues = review.code_checks(job, cv, profile, app, n)
            ai = {"strengths": [], "issues": [], "employer_restricts_ai": False}
            # Spend on the AI reviewer only once the code checks are clean, or on the last try.
            if not any(i["severity"] == "block" for i in issues) or attempt == MAX_REVISIONS:
                ai = review.ai_review(job, cv, profile, app, bool(base and not cv_from.get("changed")))
                reviewed = True
            else:
                reviewed = False
            issues += ai["issues"]
            # A weak letter is rewritten once; anything unsupported as often as allowed.
            fixable = [i for i in issues if i["where"] != "Employer rule" and
                       (i["severity"] == "block" or not polished and cover and in_letter(i) and weak(i)
                        or base and not cv_from.get("changed") and i["where"].lower() == "cv fit" and i.get("by") == "AI")]
            polished = polished or reviewed
            if not fixable or attempt == MAX_REVISIONS:
                break
            in_ans = [i for i in fixable if i["where"].lower().startswith("answer")]
            in_cov = [i for i in fixable if in_letter(i)]
            in_cv = [i for i in fixable if i not in in_ans and i not in in_cov]
            step(job_id, "Fixing what the review found")
            if in_cv:
                step(job_id, "Adapting the reused CV" if base and not cv_from.get("changed") else "Fixing what the review found")
                t = tailor.tailor(job, cv, profile, t, in_cv)
                dropped = []
                if base:
                    cv_from["changed"] = True
            if in_cov and cover:
                cover, found = write_letter(job, cv, profile, found, notes, cover, in_cov)
            if in_ans:
                drafted = tailor.draft_answers(job, cv, profile, ask, {f["label"]: drafted[f["key"]]["value"] for f in ask}, in_ans)
        if dropped:
            t["changes"] = t["changes"] + [f"Left out to fit one page: {x}" for x in dropped]
        if problem:
            issues.append({"severity": "note", "where": "Company research", "problem": problem, "suggestion": "", "by": "code"})
        if form_note:
            issues.append({"severity": "note", "where": "Form", "problem": form_note, "suggestion": "", "by": "code"})
        rv = {**ai, "issues": issues, "revisions": attempt, "pages": n, "ai_stale": False}
        with db.connect() as con:
            db.save_application(con, job_id, **app, review=rv, cv_from=cv_from)
            if old.get("cover_letter") and old["cover_letter"] != cover:
                db.save_application(con, job_id, letter_prev=old["cover_letter"])
            db.set_status(con, job_id, S.needs_review, f"{len(review.blockers({'review': rv}))} blocking issue(s)")
    except BaseException:
        with db.connect() as con:
            db.set_status(con, job_id, S.shortlisted, "tailoring stopped")
        raise


def recheck(job_id: int, **changes) -> dict:
    """Apply the user's edits, re-render, re-run code checks, and drop approval."""
    cv, profile = candidate()
    job = _job(job_id)
    if job["status"] not in (S.needs_review, S.approved, S.filling):
        raise NotReady("There is nothing to edit at this stage.")
    with db.connect() as con:
        app = db.get_application(con, job_id)
    before = review.content_hash(app)
    app.update({k: v for k, v in changes.items() if k in ("cv", "cover_letter") and v is not None})
    for k, v in (changes.get("answers") or {}).items():
        if k in app["answers"] and app["answers"][k]["policy"] != "consent" and not app["answers"][k]["policy"].startswith("sensitive"):
            app["answers"][k].update(value=[x for x in v if x.strip()], edited=True)
            if any(x.strip() for x in v):
                app["answers"][k]["basis"] = "profile"     # now the user's own words
            else:
                app["answers"][k]["basis"] = "unknown"
    n = (app["review"] or {}).get("pages")
    if changes.get("cv") is not None:
        path, n = render.render(app["cv"], cv, profile, job_id)
        app["pdf_path"] = str(path)
    if review.content_hash(app) == before:
        return app
    rv = app["review"] or {"strengths": []}
    keep = [i for i in rv.get("issues", []) if i.get("by") == "AI" or i["where"] in ("Form", "Company research")]
    app["review"] = {**rv, "issues": review.code_checks(job, cv, profile, app, n) + keep, "pages": n, "ai_stale": True}
    with db.connect() as con:
        db.save_application(con, job_id, cv=app["cv"], cover_letter=app["cover_letter"], answers=app["answers"],
                            review=app["review"], pdf_path=app["pdf_path"], approved_hash=None)
        if job["status"] != S.needs_review:
            db.set_status(con, job_id, S.needs_review, "edited after approval")
        db.log(con, job_id, "edit")
    return app


def rereview(job_id: int) -> None:
    cv, profile = candidate()
    job = _job(job_id)
    with db.connect() as con:
        app = db.get_application(con, job_id)
    ai = review.ai_review(job, cv, profile, app)
    n = app["review"].get("pages")
    form = [i for i in app["review"]["issues"] if i["where"] in ("Form", "Company research")]
    rv = {**ai, "issues": review.code_checks(job, cv, profile, app, n) + ai["issues"] + form, "pages": n, "ai_stale": False}
    with db.connect() as con:
        db.save_application(con, job_id, review=rv)


def approve(job_id: int, override: str | None = None) -> None:
    job = _job(job_id)
    with db.connect() as con:
        if job["status"] != S.needs_review:
            raise NotReady("Only an application waiting for review can be approved.")
        if tasks.get(job_id, {}).get("running"):
            raise NotReady("Wait for the current step to finish.")
        app, cap = db.get_application(con, job_id), db.settings(con)["max_per_company"]
        if company_count(con, job) >= cap:
            raise NotReady(f"You already have {cap} application(s) to {job['company']}. Change the cap on the Profile page if the employer allows more.")
        block = review.blockers(app)
        if app["review"].get("ai_stale") and any(review.needs_check(a) for a in app["answers"].values()) and \
                tasks.get(job_id, {}).get("error") and not (override or "").strip():
            raise NotReady("The AI review did not finish. Press Review again, or say why you are overriding.")
        if block and not (override or "").strip():
            raise NotReady(f"{len(block)} blocking issue(s) remain. Fix them, or say why you are overriding.")
        if not app.get("pdf_path") or not Path(app["pdf_path"]).exists():
            raise NotReady("The CV file is missing. Tailor again.")
        db.save_application(con, job_id, approved_hash=review.content_hash(app))
        db.set_status(con, job_id, S.approved, f"override: {override}" if block else None)


def _approved(job_id: int) -> tuple[dict, dict]:
    job = _job(job_id)
    with db.connect() as con:
        app = db.get_application(con, job_id)
    if job["status"] not in (S.approved, S.filling) or not review.is_approved(app):
        raise NotReady("This version has not been approved. Review and approve it first.")
    return job, app


def open_form(job_id: int) -> None:
    from .apply.session import session
    job, _ = _approved(job_id)
    if not ats.is_blocked(job["apply_url"]):
        session.open(job_id, job["apply_url"])
    else:
        raise NotReady("LinkedIn and Indeed are not automated. Apply there yourself, then mark it submitted.")
    if job["status"] == S.approved:
        with db.connect() as con:
            db.set_status(con, job_id, S.filling)


def fill_page(job_id: int) -> dict:
    from .apply.session import session
    cv, profile = candidate()
    job, app = _approved(job_id)
    fs = session.extract(job_id)
    if not fs:
        raise NotReady("No form fields found on this page. If you need to sign in or press Apply, do that first.")
    known, ask = fields.plan(fs, profile)
    stored = app["answers"] or {}
    new = [f for f in ask if f["key"] not in stored]
    usable = {**known, **{f["key"]: stored[f["key"]] for f in ask if f["key"] in stored}}
    transcript = next((p for p in sorted(config.DOCS.glob("transcript*.pdf"))), None)
    letter = None
    if app["cover_letter"] and any(f["kind"] == "file" and "cover" in f["label"].lower() for f in fs):
        letter = render.render_letter(app["cover_letter"], cv, profile, job)
    res = session.fill(job_id, fs, usable, {"cv": Path(app["pdf_path"]), "transcript": transcript, "cover_letter": letter})
    res["new_questions"] = 0
    if new:
        drafted = fields.merge(new, tailor.draft_answers(job, cv, profile, new))
        app["answers"] = {**stored, **drafted}
        fresh = [a for a in drafted.values() if a["value"]]
        rv = app["review"]
        rv["ai_stale"] = bool(fresh) or rv["ai_stale"]
        rv["issues"] = rv["issues"] + [i for a in fresh if review.needs_check(a) for i in
                                       verify.check_prose(
                                           f"Answer: {a['label'][:60]}", " ".join(a["value"]), cv, profile, job)]
        with db.connect() as con:
            db.save_application(con, job_id, answers=app["answers"], review=rv)
            if fresh:      # the content hash no longer matches, so this needs a fresh approval
                db.save_application(con, job_id, approved_hash=None)
                db.set_status(con, job_id, S.needs_review, f"{len(fresh)} new question(s) on the form")
        res["new_questions"] = len(fresh)
        if fresh:
            start(job_id, "review", rereview)    # new AI text gets the same independent review
    with db.connect() as con:
        db.log(con, job_id, "fill", detail=f"{res['filled']} filled, {len(res['needs_you'])} need you, {len(res['failed'])} failed")
    return res


def move(job_id: int, to: str, detail: str | None = None) -> None:
    """Manual status changes from the dashboard. Approval and filling have their own routes."""
    if to in ("approved", "filling", "tailoring", "needs_review"):
        raise NotReady("Use the review page for that step.")
    with db.connect() as con:
        db.set_status(con, job_id, to, detail)
