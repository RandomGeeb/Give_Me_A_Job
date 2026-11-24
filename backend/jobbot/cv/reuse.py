"""Finds a CV already written for a similar role, so the same facts are not tailored again.

The measure is what tailoring is for: of the advert's terms that your facts could answer, how many does that CV already
show? Rare terms count for more. No model is involved, and a reused CV goes through every check a new one does."""
import hashlib
import json
import math
import re
from collections import Counter

from .. import db
from ..models import APPLIED, Status as S
from . import verify
from .parse import flat

DONE = {str(s) for s in APPLIED} | {"needs_review", "approved", "filling"}
MIN_TERMS = 8       # fewer and the advert says too little to compare
STOP = set("""the and for with you our are your will this that from have has who all can not but their they was were been into than then
also more such other about using use used work working team teams role intern internship skills experience company business support
including across within new help""".split())


def stem(w: str) -> str:
    return re.sub(r"(ing|ed|es|s|ly|ment|ion|er)$", "", w) if len(w) > 5 and not re.search(r"[+#.\d]", w) else w


def words(s: str) -> set[str]:
    return {stem(w.strip(".")) for w in re.findall(r"[a-z][a-z0-9+#.]*", s.lower()) if len(w) > 1 and w not in STOP}


def rev(cv: dict) -> str:
    """Changes when your facts do, so CVs written from older facts are not reused."""
    return hashlib.sha256(json.dumps(cv, sort_keys=True).encode()).hexdigest()[:16]


def shown(t: dict) -> set[str]:
    return words(" ".join(b["text"] for e in t["entries"] for b in e["bullets"]) + " " + " ".join(i for s in t["skills"] for i in s["items"]))


def find(con, job: dict, cv: dict) -> dict | None:
    """The best existing CV for this role as {job_id, company, title, match, cv}, if it is close enough."""
    floor = db.settings(con)["reuse_percent"]
    if not floor:
        return None
    docs = {r["id"]: words(f"{r['title']} {r['description']}") for r in con.execute("SELECT id,title,description FROM jobs WHERE length(description)>300")}
    own = words(" ".join(flat(cv).values()) + " " + " ".join(i for s in cv["skills"] for i in s["items"]))
    want = docs.get(job["id"], set()) & own
    if len(want) < MIN_TERMS:
        return None
    df = Counter(w for d in docs.values() for w in d)
    weight = {w: math.log((len(docs) + 1) / (df[w] + 1)) + .05 for w in want}
    best = None
    for r in con.execute("SELECT id,company,title,status FROM jobs WHERE family=? AND id!=?", (job["family"], job["id"])).fetchall():
        a = db.get_application(con, r["id"]) if r["status"] in DONE else None
        if not a or not a["cv"] or (a["cv_from"] or {}).get("rev") != rev(cv):
            continue
        # Anything wrong with it there would be wrong here too.
        if any(i["severity"] == "block" for i in verify.check_cv(a["cv"], cv)) or \
                any(i["severity"] == "block" and re.match("cv|skills|education", i["where"], re.I) for i in (a["review"] or {}).get("issues", [])):
            continue
        has = shown(a["cv"])
        m = sum(x for w, x in weight.items() if w in has) / sum(weight.values())
        if not best or m > best["match"]:
            best = {"job_id": r["id"], "company": r["company"].strip(), "title": r["title"], "match": round(m, 3), "cv": a["cv"]}
    return best if best and best["match"] * 100 >= floor else None


# ---- the same role listed twice ----
def same_listing(a: dict, b: dict) -> bool:
    if db.norm(a["title"]) != db.norm(b["title"]) or min(len(a["description"] or ""), len(b["description"] or "")) < 400:
        return False
    city = lambda j: {db.norm(x.split(",")[0]) for x in j["locations"] or []}
    if not city(a) & city(b):
        return False
    x, y = (set(re.findall(r"\w+", j["description"].lower())) for j in (a, b))
    return len(x & y) / len(x | y) >= .95
