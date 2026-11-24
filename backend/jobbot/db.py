"""SQLite storage. Every status change goes through set_status so the event log stays complete."""
import copy
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .models import RawJob, Status, can_move

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, dupe_key TEXT NOT NULL,
  source TEXT, company TEXT, title TEXT, locations TEXT, url TEXT, apply_url TEXT, ats TEXT,
  description TEXT DEFAULT '', posted_at TEXT, deadline TEXT, family TEXT,
  status TEXT NOT NULL, filter_reason TEXT, score TEXT, band TEXT,
  next_action TEXT, notes TEXT DEFAULT '', first_seen TEXT, last_seen TEXT, updated_at TEXT,
  tier INTEGER, triage TEXT);
CREATE INDEX IF NOT EXISTS jobs_dupe ON jobs(dupe_key);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), at TEXT,
  kind TEXT, from_status TEXT, to_status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS applications(
  job_id INTEGER PRIMARY KEY REFERENCES jobs(id), cv TEXT, cover_letter TEXT, answers TEXT,
  review TEXT, pdf_path TEXT, approved_hash TEXT, updated_at TEXT,
  research TEXT, letter_notes TEXT, letter_prev TEXT, cv_from TEXT);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY, at TEXT, purpose TEXT, job_id INTEGER, model TEXT,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER, usd REAL);
CREATE TABLE IF NOT EXISTS research(k TEXT PRIMARY KEY, at TEXT, findings TEXT);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS boards(
  ats TEXT, slug TEXT, polled_at TEXT, ok INTEGER, jobs INTEGER, error TEXT, PRIMARY KEY(ats, slug));
"""
JSON_COLS = {"locations", "score", "cv", "answers", "review", "research", "triage", "cv_from"}
ADDED = {"applications": ["research TEXT", "letter_notes TEXT", "letter_prev TEXT", "cv_from TEXT"], "jobs": ["tier INTEGER", "triage TEXT"]}      # columns newer than some databases


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_ready: set[str] = set()
_init_lock = threading.Lock()


def _init() -> None:
    """Create tables once per process. Switching to WAL needs a lock that other connections can refuse."""
    with _init_lock:
        path = str(config.DB_PATH)
        if path in _ready and config.DB_PATH.exists():
            return
        config.ensure_dirs()
        con = sqlite3.connect(path, timeout=30)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            for table, cols in ADDED.items():
                have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                for col in cols:
                    if col.split()[0] not in have:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            con.commit()
        finally:
            con.close()
        _ready.add(path)


@contextmanager
def connect():
    _init()
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        d[k] = json.loads(d[k]) if d[k] else None
    return d


# ---- settings ----
def get_setting(con, k: str, default=None):
    r = con.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def put_setting(con, k: str, v) -> None:
    con.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)))


def settings(con) -> dict:
    out = copy.deepcopy(config.DEFAULT_SETTINGS)
    for k in out:
        saved = get_setting(con, k)
        if saved is None:
            continue
        out[k] = {**out[k], **saved} if isinstance(out[k], dict) else saved
    return out


# ---- jobs ----
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def dupe_key(j: RawJob) -> str:
    company = re.sub(r"\b(ltd|limited|plc|inc|llc|group|uk)\b", "", norm(j.company)).strip()
    return f"{company}|{norm(j.title)}|{norm(','.join(sorted(j.locations)))}"


def upsert_job(con, j: RawJob, status: Status, reason: str | None, family: str | None) -> tuple[int, bool]:
    """Insert or refresh a posting. Returns (id, is_new). Never touches a job's progress."""
    dk = dupe_key(j)
    r = con.execute("SELECT id, description, url, apply_url FROM jobs WHERE key=?", (j.key,)).fetchone() or \
        con.execute("SELECT id, description, url, apply_url FROM jobs WHERE dupe_key=?", (dk,)).fetchone()
    t = now()
    if r:
        con.execute("""UPDATE jobs SET last_seen=?, description=?, deadline=COALESCE(?,deadline)
                       WHERE id=?""", (t, j.description or r["description"], j.deadline, r["id"]))
        # A tracker often links to the company's own page; the job board later supplies the real form.
        if j.apply_url and j.apply_url != j.url and r["apply_url"] == r["url"]:
            con.execute("UPDATE jobs SET apply_url=?, ats=? WHERE id=?", (j.apply_url, j.ats, r["id"]))
        return r["id"], False
    cur = con.execute(
        """INSERT INTO jobs(key,dupe_key,source,company,title,locations,url,apply_url,ats,description,
           posted_at,deadline,family,status,filter_reason,first_seen,last_seen,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (j.key, dk, j.source, j.company, j.title, json.dumps(j.locations), j.url, j.apply_url or j.url,
         j.ats, j.description, j.posted_at, j.deadline, family, status, reason, t, t, t))
    log(con, cur.lastrowid, "discovered", None, status, reason or j.source)
    return cur.lastrowid, True


def log(con, job_id, kind, a=None, b=None, detail=None) -> None:
    con.execute("INSERT INTO events(job_id,at,kind,from_status,to_status,detail) VALUES(?,?,?,?,?,?)",
                (job_id, now(), kind, a, b, detail))


class BadTransition(ValueError):
    pass


def set_status(con, job_id: int, to: Status | str, detail: str | None = None) -> None:
    r = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    if r is None:
        raise KeyError(job_id)
    if not can_move(r["status"], to):
        raise BadTransition(f"cannot move from {r['status']} to {to}")
    con.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (str(Status(to)), now(), job_id))
    log(con, job_id, "status", r["status"], str(Status(to)), detail)


def get_job(con, job_id: int) -> dict | None:
    return row(con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def get_application(con, job_id: int) -> dict | None:
    return row(con.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone())


def save_application(con, job_id: int, **cols) -> None:
    con.execute("INSERT OR IGNORE INTO applications(job_id) VALUES(?)", (job_id,))
    cols["updated_at"] = now()
    for k, v in cols.items():
        con.execute(f"UPDATE applications SET {k}=? WHERE job_id=?",
                    (json.dumps(v) if k in JSON_COLS else v, job_id))


def save_score(con, job_id: int, score: dict, band: str, family: str, deadline: str | None) -> None:
    con.execute("UPDATE jobs SET score=?, band=?, family=?, deadline=COALESCE(?,deadline), updated_at=? WHERE id=?",
                (json.dumps(score), band, family, deadline, now(), job_id))
