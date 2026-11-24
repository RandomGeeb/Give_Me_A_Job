"""The model extracts evidence; this file turns it into a band and a likelihood range."""
import functools
import random
from datetime import date, datetime, timedelta, timezone

from . import db
from .cv.parse import context
from .cv.tailor import job_block
from .llm import client, prompts
from .llm.schemas import Score

BANDS = ["Reach", "Possible", "Competitive", "Strong"]
RESOLVE_DAYS = 60      # no reply after this counts as "no interview"
CALIBRATED_AT = 30


def score_job(job: dict, cv: dict, profile: dict) -> dict:
    out = client.call("score", prompts.SCORE, context(cv, profile), job_block(job) + "\n\nAssess the fit.",
                      Score, effort="low", job_id=job["id"])
    return out.model_dump()


def coverage(score: dict) -> float | None:
    pts = {"yes": 1.0, "partial": 0.5, "no": 0.0, "unknown": 0.25}
    reqs = score["requirements"]
    if not reqs:
        return None
    w = [(2 if r["essential"] else 1) for r in reqs]
    return sum(wi * pts[r["met"]] for wi, r in zip(w, reqs)) / sum(w)


def band(score: dict, posted_at: str | None, today: date | None = None) -> str:
    c = coverage(score)
    i = 1 if c is None else 0 if c < 0.45 else 1 if c < 0.65 else 2 if c < 0.85 else 3
    i -= {"normal": 0, "high": 0, "extreme": 1}[score["competition"]]
    if score["competition"] == "high":
        i = min(i, 2)
    if score["eligible"] == "unsure" or score.get("dates") == "slight":
        i = min(i, 1)
    if score["eligible"] == "no":
        i = 0
    # Many internships fill on a rolling basis, so an old posting is a weaker bet.
    if posted_at and ((today or date.today()) - date.fromisoformat(posted_at[:10])).days > 75:
        i -= 1
    return BANDS[max(0, min(3, i))]


def out_of_scope(score: dict) -> str | None:
    if not score["is_relevant_internship"]:
        return "not a relevant internship"
    if score["eligible"] == "no":
        return "not eligible"
    return "the dates do not fit your availability" if score.get("dates") == "conflict" else None


def tier(score: dict, band_: str) -> int:
    """1 is the best chance, 4 is out of scope."""
    return 4 if out_of_scope(score) else {"Strong": 1, "Competitive": 1, "Possible": 2, "Reach": 3}[band_]


@functools.lru_cache(256)
def _interval(a: float, b: float) -> tuple[float, float]:
    rng = random.Random(7)
    xs = sorted(rng.betavariate(a, b) for _ in range(6000))
    return xs[600], xs[5400]    # 80% interval


def estimate(prior: float, weight: float, hits: int, n: int) -> dict:
    a, b = prior * weight + hits, (1 - prior) * weight + n - hits
    lo, hi = _interval(round(a, 4), round(b, 4))
    return {"mean": a / (a + b), "low": lo, "high": hi, "hits": hits, "n": n}


def outcomes(con) -> list[dict]:
    """One row per submitted application whose interview outcome is known."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RESOLVE_DAYS)).isoformat()
    out = []
    for j in con.execute("SELECT id,band,family,status FROM jobs"):
        ev = con.execute("SELECT to_status,at FROM events WHERE job_id=? AND kind='status' ORDER BY id", (j["id"],)).fetchall()
        reached = {e["to_status"] for e in ev}
        sub = next((e["at"] for e in ev if e["to_status"] == "submitted"), None)
        if not sub or j["status"] == "withdrawn" and not reached & {"interview", "offer"}:
            continue
        interview = bool(reached & {"interview", "offer"})
        stale = j["status"] in ("submitted", "assessment") and sub < cutoff
        out.append({"job_id": j["id"], "band": j["band"], "family": j["family"], "interview": interview,
                    "offer": "offer" in reached,
                    "resolved": interview or j["status"] == "rejected" or stale,
                    "offer_resolved": "offer" in reached or j["status"] == "rejected" or stale})
    return out


def likelihoods(con) -> dict:
    s, res = db.settings(con), outcomes(con)
    n = sum(r["resolved"] for r in res)
    bands = {}
    for b in BANDS:
        rs = [r for r in res if r["band"] == b]
        ri, ro = [r for r in rs if r["resolved"]], [r for r in rs if r["offer_resolved"]]
        p = s["priors"][b]
        bands[b] = {"interview": estimate(p["interview"], s["prior_weight"], sum(r["interview"] for r in ri), len(ri)),
                    "offer": estimate(p["offer"], s["prior_weight"], sum(r["offer"] for r in ro), len(ro))}
    return {"bands": bands, "resolved": n, "needed": CALIBRATED_AT,
            "basis": "your outcomes" if n >= CALIBRATED_AT else "assumption-based"}
