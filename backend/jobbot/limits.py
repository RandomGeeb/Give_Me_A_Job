"""Employers that limit how many times you may apply, and how many applications you have made. Read from adverts by code, not by Claude."""
import re
from datetime import date, timedelta

from . import db
from .models import Status as S

NUM = {w: i for i, w in enumerate("one two three four five six seven eight nine ten".split(), 1)} | {"single": 1, "once": 1}
N = r"(?:\w+ ?\()?(?P<n>one|two|three|four|five|six|seven|eight|nine|ten|single|10|[1-9])\)?"
THING = r"(?:applications?|positions?|roles?|jobs?|vacanc(?:y|ies)|programmes?|programs?|schemes?|internships?|opportunit(?:y|ies)|openings?|locations?|combinations?)\b"
FEW = r"(?:[\w/&-]+ (?:/ )?){0,4}?"
# Each needs the sentence to be about applying, which `find` checks.
RULES = [re.compile(p, re.I) for p in (
    rf"\b(?:up to|(?:a )?max(?:imum)?(?: of)?|no more than|at most|limited? to|limit of|limit (?:your )?applications? to)\s+(?:a |the )?{N}\s+{FEW}{THING}",
    rf"\bonly (?:be able to )?(?:apply|submit|make|accept|consider|enter)\w*\s+(?:(?:for|to|in|an?|the|your)\s+){{0,3}}{N}\s+{FEW}{THING}",
    rf"\b(?:apply|submit|make|accept|consider)\s+(?:(?:for|to|the)\s+){{0,2}}only\s+(?:(?:for|to)\s+)?{N}\s+{FEW}{THING}",
    rf"\b(?:accept|consider|make|submit)\s+(?:only )?{N}\s+applications?\b(?=.*\b(?:per|each|in \d+|within|every)\b|\s+if you|\s*$)",
    rf"\b(?:apply|submit)\s+(?:for|to)\s+(?:the\s+)?{N}\s+{FEW}{THING}(?=.*\b(?:most|best)\b)",
    rf"\b(?P<n>one|1) application (?:per|for each) (?:candidate|applicant|person|student|individual|recruit|cycle|year|season|campaign)",
    rf"\b(?:can|may|must|should) (?:only )?(?:apply|submit an application) (?P<n>once)\b",
)]
# These mean one, without a number to read.
ONE = re.compile(r"more than one (?:application|role|position|programme|program|location|job)\b.*\b(?:first|last|only|not|withdraw\w*|reject\w*|disregard\w*|disqualif\w*|delay\w*|discount\w*)\b|"
                 r"\b(?:not(?! need| have to)|cannot|can't|unable to|do not(?! need| have to)|don't(?! need| have to)|won't)\b[^.]{0,50}\b(?:multiple applications|more than one (?:application|role|position|programme|program|location|job))|"
                 r"multiple applications\b[^.]{0,40}\b(?:not (?:be )?(?:accepted|considered|permitted|allowed)|reject\w*|withdraw\w*|disqualif\w*)|"
                 r"must not have applied (?:for|to) any other", re.I)
ABOUT = re.compile(r"\bappl(?:y|ies|ied|ying|ications?|icants?)\b", re.I)
NOT = re.compile(r"cover letter|reasonable (?:accommodation|adjustment)|references? (?:letter|from)|minutes|\bpages?\b|software applications", re.I)
EACH = re.compile(r"(?:rolling|within|in|per|every|any|each)\W+(?:(?:a|an|any|the|given|rolling|period of)\s+)*(\d+|one|two|three|four|five|six|nine|twelve)?[\s-]*(day|week|month|year)s?\b", re.I)
UNIT = {"day": 1, "week": 7, "month": 30.5, "year": 365}
CYCLE = 365            # "per recruitment cycle", or no period given
LIVE = {S.approved, S.filling}
DRAFT = {S.shortlisted, S.tailoring, S.needs_review}
SAME = {"mitsubishi ufg": "mufg", "bytedance": "tiktok", "sig": "susquehanna international"}


def key(company: str, job_id: int | None = None) -> str:
    """One name for an employer, however a job board writes it."""
    s = re.sub(r"\(.*?\)|, an? .*? company$|\.(?:com|io|ai)\b", " ", company.lower())
    s = db.norm(s)
    if s == "company not shown":
        return f"unknown {job_id}"
    s = re.sub(r"\b(?:ltd|limited|plc|inc|llc|group|uk|the|industries|internships?|programmes?|programs?|early careers?|careers?|university jobs|jobs?|board)\b|\b\d+$", " ", s)
    s = re.sub(r"\s+", " ", s).strip() or db.norm(company)
    return SAME.get(s, s)


def tight(x: dict) -> tuple:
    """Fewest first; then one that says its period over one that does not; then the longest."""
    return x["n"], not x["stated"], -x["days"]


def find(text: str | None) -> dict | None:
    """The strictest limit stated: {n, days, quote}."""
    best = None
    for line in re.split(r"(?<=[.!?;])\s+|\n+|[•*]{1,3}\s", re.sub(r"<[^>]+>", " ", text or "")):
        line = re.sub(r"\s+", " ", line).strip(" -\"'")
        if not ABOUT.search(line) or NOT.search(line) and not re.search(r"\bapplications?\b.*\b(?:limit|maximum|up to)", line, re.I):
            continue
        n = min((NUM.get(m["n"].lower()) or int(m["n"]) for rx in RULES if (m := rx.search(line))), default=None)
        if n is None and ONE.search(line):
            n = 1
        if n is None:
            continue
        days = None
        if (m := EACH.search(line)) and (m[1] or m[2].lower() != "year"):
            days = round((NUM.get((m[1] or "1").lower()) or (12 if (m[1] or "").lower() == "twelve" else int(m[1] or 1))) * UNIT[m[2].lower()])
        got = {"n": n, "days": days or CYCLE, "stated": days is not None}
        if best is None or tight(got) < tight(best):
            best = got |  {"quote": line if len(line) <= 300 else line[:297] + "…"}
    return best


_seen: dict[int, tuple] = {}


def _stated(con, rows) -> dict[int, dict]:
    """Limits by job. Each advert is only read again when it changes."""
    out = {}
    for r in rows:
        mark = (str(db.config.DB_PATH), r["key"], r["nd"], r["ns"])
        if _seen.get(r["id"], (None,))[0] != mark:
            d = db.row(con.execute("SELECT description,score FROM jobs WHERE id=?", (r["id"],)).fetchone())
            found = [x for x in (find(d["description"]), find((d["score"] or {}).get("application_limit"))) if x]
            _seen[r["id"]] = (mark, min(found, key=tight) if found else None)
        if _seen[r["id"]][1]:
            out[r["id"]] = _seen[r["id"]][1]
    return out


def _submitted(con, rows) -> dict[int, str]:
    """When each role was sent. Withdrawn before sending is not an application; withdrawn afterwards is."""
    sent: dict[int, str] = {}
    for e in con.execute("SELECT job_id,at,from_status,to_status FROM events WHERE kind='status' ORDER BY id"):
        if e["to_status"] == S.submitted and e["from_status"] != S.rejected:
            sent[e["job_id"]] = e["at"][:10]
        elif e["to_status"] == S.discovered:         # put back to the start, so it had been marked by mistake
            sent.pop(e["job_id"], None)
    now = {r["id"]: r["status"] for r in rows}
    return {i: d for i, d in sent.items() if now.get(i) in (S.submitted, S.assessment, S.interview, S.offer, S.rejected, S.withdrawn, S.closed)}


def employers(con) -> dict[str, dict]:
    rows = con.execute("SELECT id,key,company,title,status,deadline,length(description) nd,length(score) ns FROM jobs").fetchall()
    stated, sent = _stated(con, rows), _submitted(con, rows)
    mine = db.get_setting(con, "employer_limits", {})
    out: dict[str, dict] = {}

    def at(k, company):
        return out.setdefault(k, {"key": k, "company": company.strip(), "limit": None, "advert": None, "applied": [], "live": [], "drafts": []})
    for r in rows:
        e = at(key(r["company"], r["id"]), r["company"])
        who = {"id": r["id"], "title": r["title"], "status": r["status"]}
        if r["id"] in sent:
            e["applied"].append({**who, "company": r["company"].strip(), "date": sent[r["id"]], "outside": None})
        elif r["status"] in LIVE:
            e["live"].append(who)
        elif r["status"] in DRAFT:
            e["drafts"].append(who)
        if (x := stated.get(r["id"])) and (not e["advert"] or tight(x) < tight(e["advert"])):
            e["advert"] = {**x, "job": r["id"]}
    for o in db.get_setting(con, "outside_apps", []):
        at(key(o["company"]), o["company"])["applied"].append({"id": None, "title": o["title"], "status": "submitted", "company": o["company"], "date": o["date"], "outside": o["id"]})
    today = date.today()
    for k, e in out.items():
        own = mine.get(k)
        e["limit"] = ({"n": own["n"], "days": own["days"], "quote": "", "by": "you"} if own["n"] else None) if own else {**e["advert"], "by": "advert"} if e["advert"] else None
        e["yours"] = bool(own)
        e["applied"].sort(key=lambda a: a["date"], reverse=True)
        lim = e["limit"]
        e["used"] = e["left"] = e["free_on"] = None
        if lim:
            since = (today - timedelta(days=lim["days"])).isoformat()
            recent = [a for a in e["applied"] if a["date"] > since]
            e["used"], e["left"] = len(recent), max(0, lim["n"] - len(recent))
            if not e["left"] and lim["days"] != CYCLE:          # the one whose expiry brings you back under the limit
                e["free_on"] = (date.fromisoformat(recent[lim["n"] - 1]["date"]) + timedelta(days=lim["days"])).isoformat()
    return out


def status(e: dict, job_id: int) -> dict | None:
    """What a role's limit means for you now: reached, taken (promised to another role), pick (more roles than places) or ok."""
    if not e["limit"]:
        return None
    others = [x for x in e["live"] if x["id"] != job_id]
    sent = any(a["id"] == job_id for a in e["applied"])
    going = {x["id"] for x in e["live"] + e["drafts"]} | ({job_id} if not sent else set())
    state = "sent" if sent else "reached" if not e["left"] else "taken" if len(others) >= e["left"] else "pick" if len(going) > e["left"] else "ok"
    return {"state": state, "n": e["limit"]["n"], "days": e["limit"]["days"], "used": e["used"], "left": e["left"], "going": len(going),
            "quote": e["limit"]["quote"], "by": e["limit"]["by"], "free_on": e["free_on"], "others": [x["title"] for x in others]}


def of(con, job: dict) -> dict:
    return employers(con)[key(job["company"], job["id"])]


def period(days: int) -> str:
    return "per recruitment cycle" if days == CYCLE else f"in any {days} days" if days < 60 else f"in any {round(days / 30.5)} months"


def refuse(con, job: dict, cap: int, approving: bool) -> str | None:
    """Why this role should not go ahead, if it should not. approving: also count roles approved but not sent yet."""
    e = of(con, job)
    s = status(e, job["id"])
    if s and s["state"] == "reached":
        return (f"{e['company']} allows {s['n']} application{'s' * (s['n'] > 1)} {period(s['days'])} and you have made {s['used']}. "
                "If that is wrong, change the limit on the Applied page.")
    if s and approving and s["state"] == "taken":
        return (f"{e['company']} allows {s['n']} application{'s' * (s['n'] > 1)} {period(s['days'])}, and {' and '.join(s['others'])} is already approved. "
                "Move that one back, or change the limit on the Applied page.")
    n = len([a for a in e["applied"] if a["id"] != job["id"]]) + len([x for x in e["live"] if x["id"] != job["id"]])
    if approving and n >= cap:
        return f"You already have {n} application(s) to {e['company']}, and your own cap is {cap}. Change the cap on the Profile page if the employer allows more."
    return None
