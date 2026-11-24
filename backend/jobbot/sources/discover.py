"""Run every source, classify, and store. Safe to re-run; progress on existing jobs is never touched."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .. import costs, db, filters
from ..llm.client import LLMError
from ..models import RawJob, Status
from . import ats, simplify
from .http import client

state = {"running": False, "done": 0, "total": 0, "phase": "idle", "last": None, "errors": []}
_lock = threading.Lock()


def store(con, jobs: list[RawJob], fams: list[str], regions: list[str] = ("uk", "sg")) -> dict:
    n = {"seen": 0, "new": 0, "kept": 0}
    for j in jobs:
        fam, reason = filters.classify(j, fams, regions)
        n["seen"] += 1
        # Keeping every US job on 1,000 boards would bury the useful rejections.
        if reason in (filters.OUTSIDE, "not an internship or placement"):
            continue
        _, new = db.upsert_job(con, j, Status.filtered_out if reason else Status.discovered, reason, fam)
        n["new"] += new
        n["kept"] += new and not reason
    return n


def run(max_age_hours: int = 20, limit_boards: int | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("discovery is already running")
    total = {"seen": 0, "new": 0, "kept": 0}
    state.update(running=True, done=0, total=0, phase="tracker", errors=[])

    def add(n):
        for k in total:
            total[k] += n[k]

    try:
        with client() as c:
            with db.connect() as con:
                fams, regs = (db.settings(con)[k] for k in ("families", "regions"))
            try:
                jobs, boards = simplify.fetch(c)
                with db.connect() as con:
                    add(store(con, jobs, fams, regs))
                    for a, s in boards:
                        con.execute("INSERT OR IGNORE INTO boards(ats,slug) VALUES(?,?)", (a, s))
            except Exception as e:  # one dead source must not stop the others
                state["errors"].append(f"simplify: {e!r}"[:200])
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
            # Boards that failed last time are retried fortnightly.
            stale = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
            with db.connect() as con:
                todo = con.execute(
                    """SELECT ats,slug FROM boards WHERE polled_at IS NULL OR (polled_at < ? AND (ok=1 OR polled_at < ?))
                       ORDER BY jobs DESC LIMIT ?""", (cutoff, stale, limit_boards or -1)).fetchall()
            state.update(phase="boards", total=len(todo))

            def poll(b):
                try:
                    return b, ats.POLL[b["ats"]](c, b["slug"]), None
                except Exception as e:
                    return b, [], f"{type(e).__name__}: {e}"[:160]

            with ThreadPoolExecutor(8) as ex:
                for b, jobs, err in ex.map(poll, todo):
                    with db.connect() as con:
                        add(store(con, jobs, fams, regs))
                        con.execute("UPDATE boards SET polled_at=?, ok=?, jobs=?, error=? WHERE ats=? AND slug=?",
                                    (db.now(), int(err is None), len(jobs), err, b["ats"], b["slug"]))
                    state["done"] += 1
        state["last"] = {**total, "at": db.now()}
        return total
    finally:
        state.update(running=False, phase="idle")
        _lock.release()


def run_web() -> dict:
    """The paid search. Separate from run() so it only ever happens when asked for."""
    from . import websearch
    if not _lock.acquire(blocking=False):
        raise RuntimeError("discovery is already running")
    state.update(running=True, done=0, total=0, phase="web", errors=[])
    try:
        res = websearch.run(state)
        state["last"] = {**res, "at": db.now()}
        return res
    except (costs.LimitReached, LLMError) as e:
        state["errors"].append(str(e))
        raise
    finally:
        state.update(running=False, phase="idle")
        _lock.release()


def reclassify() -> int:
    """Re-run the filters over jobs nobody has acted on, e.g. after switching role families."""
    changed = 0
    with db.connect() as con:
        fams, regs = (db.settings(con)[k] for k in ("families", "regions"))
        for r in con.execute("SELECT * FROM jobs WHERE status IN ('discovered','filtered_out') AND source!='manual'").fetchall():
            d = db.row(r)
            fam, reason = filters.classify(RawJob(key=d["key"], source=d["source"], company=d["company"],
                                                  title=d["title"], locations=d["locations"], url=d["url"]), fams, regs)
            if d["tier"] == 4 and not reason:
                continue        # rated out of scope from the advert, which these filters have not read
            con.execute("UPDATE jobs SET family=?, filter_reason=? WHERE id=?", (fam, reason, d["id"]))
            to = Status.filtered_out if reason else Status.discovered
            if to != d["status"]:
                db.set_status(con, d["id"], to, reason or "filters changed")
                changed += 1
    return changed


def add_url(url: str, company: str, title: str, location: str, description: str, deadline: str | None) -> int:
    a, slug, jid = ats.parse_url(url)
    j = RawJob(key=ats.job_key(url), source="manual", ats=a, company=company, title=title,
               locations=[location or "United Kingdom"], url=url, apply_url=ats.apply_url(a, slug, jid, url),
               description=description, deadline=deadline or None)
    with db.connect() as con:
        # Added by hand, so it is never filtered out.
        jid_, _ = db.upsert_job(con, j, Status.discovered, None, filters.family(title) or "software")
        return jid_
