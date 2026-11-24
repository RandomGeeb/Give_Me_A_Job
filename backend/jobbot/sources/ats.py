"""Recognise applicant-tracking systems from URLs and poll their public job-board APIs."""
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from ..models import RawJob
from .http import get_json

NAMES = ("myworkday", "oraclecloud", "smartrecruiters", "workable", "icims", "successfactors", "teamtailor")
BLOCKED = re.compile(r"(^|\.)(linkedin\.com|indeed\.(com|co\.uk))$")


def strip_html(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)[^>]*>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", "", html.unescape(s))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\xa0]+", " ", s)).strip()


def parse_url(url: str) -> tuple[str, str | None, str | None]:
    """(ats, board slug, job id); slug/id are None where the URL does not contain them."""
    u = urlparse(url)
    host, q = u.netloc.lower(), parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("greenhouse.io"):
        if "for" in q:
            return "greenhouse", q["for"][0], (q.get("token") or q.get("gh_jid") or [None])[0]
        if len(parts) >= 3 and parts[1] == "jobs":
            return "greenhouse", parts[0], parts[2]
    if "gh_jid" in q:
        return "greenhouse", None, q["gh_jid"][0]
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return "lever", ("eu:" if ".eu." in host else "") + parts[0], parts[1] if len(parts) > 1 else None
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", parts[0], parts[1] if len(parts) > 1 else None
    for n in NAMES:
        if n in host:
            return n, None, None
    return "other", None, None


def job_key(url: str) -> str:
    ats, slug, jid = parse_url(url)
    if jid and ats in ("greenhouse", "lever", "ashby"):
        return f"{ats}:{jid}"
    u = urlparse(url)
    keep = "&".join(p for p in sorted(u.query.split("&"))
                    if p and not re.match(r"(utm_|ref|src|source|gh_src|lever-source|embed)", p, re.I))
    return f"url:{u.netloc.lower().removeprefix('www.')}{u.path.rstrip('/')}" + (f"?{keep}" if keep else "")


def is_blocked(url: str) -> bool:
    return bool(BLOCKED.search(urlparse(url).netloc.lower()))


def apply_url(ats: str, slug: str | None, jid: str | None, url: str) -> str:
    if ats == "greenhouse" and slug and jid:
        if ".eu.greenhouse.io" in url:
            return f"https://job-boards.eu.greenhouse.io/embed/job_app?for={slug}&token={jid}"
        return f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={jid}"
    if ats == "lever" and slug and jid:
        host = "jobs.eu.lever.co" if slug.startswith("eu:") else "jobs.lever.co"
        return f"https://{host}/{slug.removeprefix('eu:')}/{jid}/apply"
    if ats == "ashby" and slug and jid:
        return f"https://jobs.ashbyhq.com/{slug}/{jid}/application"
    return url


def _ms(ms) -> str | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat() if ms else None


def greenhouse(c: httpx.Client, slug: str) -> list[RawJob]:
    d = get_json(c, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    out = []
    for x in d["jobs"]:
        jid = str(x["id"])
        out.append(RawJob(
            key=f"greenhouse:{jid}", source="greenhouse", ats="greenhouse",
            company=x.get("company_name") or slug, title=x["title"].strip(),
            locations=[(x.get("location") or {}).get("name") or ""], url=x["absolute_url"],
            apply_url=apply_url("greenhouse", slug, jid, x["absolute_url"]),
            posted_at=(x.get("first_published") or "")[:10] or None,
            deadline=(x.get("application_deadline") or "")[:10] or None))
    return out


def lever(c: httpx.Client, slug: str) -> list[RawJob]:
    eu = slug.startswith("eu:")
    s = slug.removeprefix("eu:")
    d = get_json(c, f"https://api.{'eu.' if eu else ''}lever.co/v0/postings/{s}?mode=json")
    return [RawJob(
        key=f"lever:{x['id']}", source="lever", ats="lever", company=s, title=x["text"].strip(),
        locations=(x.get("categories") or {}).get("allLocations") or [(x.get("categories") or {}).get("location") or ""],
        url=x["hostedUrl"], apply_url=x.get("applyUrl") or apply_url("lever", slug, x["id"], x["hostedUrl"]),
        posted_at=_ms(x.get("createdAt")), description=_lever_text(x)) for x in d]


def _lever_text(x: dict) -> str:
    parts = [x.get("descriptionPlain") or ""]
    parts += [f"{sec.get('text', '')}\n{strip_html(sec.get('content', ''))}" for sec in x.get("lists") or []]
    parts.append(x.get("additionalPlain") or "")
    return "\n\n".join(p for p in parts if p.strip())


def ashby(c: httpx.Client, slug: str) -> list[RawJob]:
    d = get_json(c, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    out = []
    for x in d["jobs"]:
        if x.get("isListed") is False:
            continue
        locs = [x.get("location") or ""] + [s.get("location", "") for s in x.get("secondaryLocations") or []]
        addr = ((x.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
        if addr and locs[0]:
            locs[0] = f"{locs[0]}, {addr}"
        out.append(RawJob(
            key=f"ashby:{x['id']}", source="ashby", ats="ashby", company=slug, title=x["title"].strip(),
            locations=[l for l in locs if l], url=x["jobUrl"], apply_url=x.get("applyUrl") or x["jobUrl"],
            posted_at=(x.get("publishedAt") or "")[:10] or None, description=x.get("descriptionPlain") or ""))
    return out


POLL = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch_description(c: httpx.Client, ats: str, url: str, apply: str) -> str:
    """Full text for one posting where a public API has it; '' otherwise."""
    for u in (apply, url):
        a, slug, jid = parse_url(u)
        if not (slug and jid):
            continue
        if a == "greenhouse":
            return strip_html(get_json(c, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}")["content"])
        if a == "lever":
            h = "api.eu.lever.co" if slug.startswith("eu:") else "api.lever.co"
            return _lever_text(get_json(c, f"https://{h}/v0/postings/{slug.removeprefix('eu:')}/{jid}?mode=json"))
        if a == "ashby":
            for j in ashby(c, slug):
                if j.key == f"ashby:{jid}":
                    return j.description
    return ""
