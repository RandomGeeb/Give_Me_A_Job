"""Takes an approved application through its pages: fill, move on, fill the next. Never signs in, ticks a declaration or submits."""
import re
import threading
import time
import traceback
from urllib.parse import urlparse

from playwright.sync_api import Error as PWError

from . import costs, db, pipeline
from .apply import fields
from .llm.client import LLMError
from .models import APPLIED
from .models import Status as S
from .sources import ats

WAIT, LIMIT = 2.0, 45 * 60          # seconds between looks, and how long to wait for you on one page
walks: dict[int, dict] = {}
_lock = threading.Lock()
GONE = ("The application window is closed", "has been closed", "Target closed")


def state(job_id: int) -> dict | None:
    return walks.get(job_id)


def stop(job_id: int) -> None:
    if job_id in walks:
        walks[job_id]["stop"] = True


def start(job_id: int) -> None:
    pipeline._approved(job_id)
    with _lock:
        if walks.get(job_id, {}).get("running"):
            raise pipeline.NotReady("This application is being filled already.")
        walks[job_id] = {"running": True, "stop": False, "state": "filling", "message": "Opening the form", "step": 0, "of": 0, "name": "", "pages": []}
    threading.Thread(target=_run, args=(job_id,), daemon=True).start()


def set_link(job_id: int, url: str) -> dict:
    """Use your own link to the application form. Blank goes back to the one from the advert."""
    from .apply.session import session
    job, w = pipeline._job(job_id), walks.get(job_id) or {}
    if job["status"] in APPLIED:
        raise pipeline.NotReady("This one is already submitted.")
    if w.get("running") and w["state"] == "filling":
        raise pipeline.NotReady("A page is being filled. Press Stop filling first.")
    url = url.strip()
    if url and "://" not in url:
        url = "https://" + url
    try:
        u = urlparse(url or job["url"])
        ok = u.scheme in ("http", "https") and "." in (u.hostname or "") and not u.username and not re.search(r"\s", url)
    except ValueError:
        ok = False
    if not ok:
        raise pipeline.NotReady("That is not a web address. Paste the whole link, starting with https://")
    if ats.is_blocked(url):
        raise pipeline.NotReady("LinkedIn and Indeed are not automated. Paste the link to the employer's own form.")
    # A Workday link may be to its CV reader or your last application; the plain form is the one that gets filled.
    url = re.sub(r"(myworkday(?:jobs|site)\.com/.*/apply)/(?:autofillWithResume|useMyLastApplication)", r"\1/applyManually", url)
    a, slug, jid = ats.parse_url(url or job["url"])
    new = ats.apply_url(a, slug, jid, url or job["url"])
    with db.connect() as con:
        same = con.execute("SELECT id,company,title,status FROM jobs WHERE id!=? AND (url IN (?,?) OR apply_url IN (?,?)) ORDER BY status='filtered_out' LIMIT 1",
                           (job_id, new, url or new, new, url or new)).fetchone()
        if new != job["apply_url"]:
            con.execute("UPDATE jobs SET apply_url=?, ats=?, updated_at=? WHERE id=?", (new, a, db.now(), job_id))
            db.log(con, job_id, "link", detail=f"Form link changed to {urlparse(new).hostname}")
    if new != job["apply_url"]:
        w["moved"] = True
        session.go(job_id, new)
    return {"apply_url": new, "same": dict(same) if same else None}


def _run(job_id: int) -> None:
    w = walks[job_id]
    try:
        pipeline.open_form(job_id)
        _walk(job_id, w)
    except (pipeline.NotReady, LLMError, costs.LimitReached) as e:
        w |= {"state": "stopped", "message": str(e)}
    except Exception as e:
        closed = any(g in str(e) for g in GONE)
        if not closed:
            traceback.print_exc()
        w |= {"state": "stopped", "message": "The form window was closed." if closed else f"Stopped: {str(e)[:200]}"}
    finally:
        w["running"] = False
        try:
            if any(p["new_questions"] for p in w["pages"]):
                pipeline.review_later(job_id)
        except Exception:
            traceback.print_exc()


def _walk(job_id: int, w: dict) -> None:
    from .apply.session import session
    done: list[dict] = []                # pages already filled: {step, url, labels}
    here, since, begun, seen = None, time.monotonic(), 0, None

    def wait_for_you(kind: str, message: str):
        w.update(state=kind, message=message)

    while True:
        if w["stop"]:
            return wait_for_you("stopped", "Stopped. What was filled is still on the form.")
        with db.connect() as con:
            job = db.get_job(con, job_id)
        if job["status"] not in (S.approved, S.filling):
            return wait_for_you("stopped", "Stopped because this application's status changed.")
        if time.monotonic() - since > LIMIT:
            return wait_for_you("stopped", "Stopped after waiting 45 minutes. Press Fill application to carry on from here.")
        if w.pop("moved", False):             # you gave it another link, so start again from there
            done, here, begun = [], None, 0
        try:
            s = session.step(job_id)
        except PWError as e:
            if any(g in str(e) for g in GONE):
                raise
            time.sleep(WAIT)              # between pages
            continue
        w.update(step=s["step"], of=s["of"], name=s["name"] or s["heading"])
        at = (s["url"], s["step"], s["name"])
        if at != seen:
            seen, since = at, time.monotonic()
        if ats.is_blocked(s["url"]):
            return wait_for_you("stopped", "This goes to LinkedIn or Indeed, which are not automated. Apply there yourself.")
        if s["already"]:
            return wait_for_you("stopped", "This site says you have already applied for this role.")
        if s["signin"]:
            wait_for_you("signin", "Sign in or create your account in the form window. Filling carries on by itself afterwards.")
            time.sleep(WAIT)
            continue
        if s["review"]:
            return wait_for_you("review", "Every page is filled. Read through the review page, then submit it yourself.")
        if (s["start"] or s["apply"]) and begun < 3 and not (s["apply"] and ats.is_blocked(s["apply"])):
            begun += 1
            w.update(state="filling", message="Opening the application")
            session.begin(job_id, s)
            continue
        fs = session.extract(job_id, False)
        labels = {f["key"] for f in fs}
        page = _known(done, at, labels)
        # Inside Workday only its numbered steps are the form. Elsewhere, a page after the first can be short.
        form = s["workday"] or not s["host"] and (_application(fs) or bool(done and fs and (s["of"] or s["next"])))
        if page is None and not form:
            wait_for_you("waiting", "No application form on this page yet. Press Apply or carry on in the form window; filling starts when the form appears.")
            time.sleep(WAIT)
            continue
        if page is not None:              # filled already, so this is your turn (or you went back)
            if page is not here:
                wait_for_you("waiting", "You went back to a page that is already filled. Press its Next button when you are ready.")
                here = page
            time.sleep(WAIT)
            continue
        here = {"at": at, "labels": labels}
        w.update(state="filling", message="Reading the page")
        _settled(session, job_id)
        now = session.step(job_id)
        if now["signin"] or now["review"] or (now["url"], now["step"], now["name"]) != at:
            continue                       # it turned out to be something else once it had loaded
        r = _fill(job_id, w, s)
        here["labels"] |= set(r["keys"])
        done.append(here)
        since = time.monotonic()
        if r["draft_error"] and r["blocking"]:
            wait_for_you("waiting", f"Some questions could not be answered: {r['draft_error']} Answer the red fields, then press the form's Next button.")
        elif r["blocking"]:
            wait_for_you("waiting", f"{len(r['blocking'])} field{'s' * (len(r['blocking']) > 1)} on this page need{'s' * (len(r['blocking']) == 1)} you. "
                         + _then(s))
            if s["submit"] and not s["next"] and not s["of"]:
                w["state"] = "review"
                return             # the whole form is on this page
        else:
            s = session.step(job_id)
            if s["safe"] and session.press_next(job_id):
                if not _moved(session, job_id, at, w):
                    errs = session.step(job_id)["errors"]
                    w["pages"][-1]["errors"] = errs
                    wait_for_you("waiting", ("The site would not accept this page: " + "; ".join(errs)[:300] + ". Fix it" if errs else
                                             "The form did not move on. Check this page") + ", then press its Next button.")
            elif s["next"]:
                wait_for_you("waiting", "This page is filled. Check it and press its Next button; the next page fills by itself.")
            elif s["submit"] or not s["of"]:
                return wait_for_you("review", "The form is filled. Check it, then submit it yourself.")
            else:
                wait_for_you("waiting", "This page is filled. Check it and carry on; the next page fills by itself.")


def _then(s: dict) -> str:
    return "Answer the red ones and press its Next button; the next page fills by itself." if s["next"] or s["of"] else \
        "Answer the red ones, check the rest, then submit it yourself."


def _known(done: list[dict], at: tuple, labels: set) -> dict | None:
    """The page this is, if it has been filled. A step name is trusted over the questions, which can change as you answer."""
    for p in reversed(done):
        if at[1] and p["at"][1]:
            if p["at"] == at:
                return p
        elif p["at"][0] == at[0] and (not labels or len(labels & p["labels"]) * 2 >= len(labels)):
            return p
    return None


def _application(fs: list[dict]) -> bool:
    """Not a job search page, a newsletter box or an advert."""
    return fields.looks_like_application(fs) or len(fs) > 3


def _settled(session, job_id: int) -> None:
    """Wait for a page to finish drawing, so it is never taken for an empty one and passed over."""
    session.settle(job_id)
    last = None
    for _ in range(12):
        now = {f["key"] for f in session.extract(job_id, False)}
        if now and now == last:
            return
        last = now
        time.sleep(WAIT / 2)


def _fill(job_id: int, w: dict, s: dict) -> dict:
    """Answering one question can bring up another, so look again until nothing new appears."""
    from .apply.session import session
    total, name = None, s["name"] or s["heading"] or f"Page {len(w['pages']) + 1}"
    for _ in range(3):
        w.update(state="filling", message=f"Filling {name}")
        try:
            r = pipeline.fill_page(job_id, frozenset(total["keys"] if total else ()), then_review=False)
        except pipeline.NotReady as e:
            if total is None and str(e).startswith("No form fields"):
                r = {"filled": 0, "kept": 0, "check": [], "needs_you": [], "failed": [], "blocking": [], "keys": [], "left": [], "new_questions": 0, "draft_error": None}
            else:
                raise
        if total is None:
            total = r
            w["pages"].append({"page": name, "errors": []})
        else:
            for k in ("filled", "kept", "check", "needs_you", "failed", "blocking", "new_questions"):
                total[k] += r[k]
            total["draft_error"] = total["draft_error"] or r["draft_error"]
            total["keys"] = sorted({*total["keys"], *r["keys"]})
        w["pages"][-1] |= {k: total[k] for k in ("filled", "kept", "check", "needs_you", "failed", "left", "new_questions", "draft_error")}
        if {f["key"] for f in session.extract(job_id, False)} <= set(total["keys"]):
            break
    return total


def _moved(session, job_id: int, at: tuple, w: dict) -> bool:
    w.update(message="Moving to the next page")
    for _ in range(12):
        time.sleep(WAIT)
        try:
            s = session.step(job_id)
        except PWError:
            continue
        if (s["url"], s["step"], s["name"]) != at:
            return True
        if s["errors"]:
            return False
    return False
