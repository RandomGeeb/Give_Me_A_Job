"""The open Simplify tracker: UK listings, plus the names of job boards worth polling directly."""
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ..models import RawJob
from . import ats
from .seeds import BOARDS
from .http import get_json

URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"


def guess(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for p in ("www.", "careers.", "jobs."):
        host = host.removeprefix(p)
    return host.split(".")[0]


def fetch(c: httpx.Client) -> tuple[list[RawJob], set[tuple[str, str]]]:
    data = get_json(c, URL)
    jobs, boards = [], set(BOARDS)
    for x in data:
        a, slug, jid = ats.parse_url(x["url"])
        if slug and a in ats.POLL:
            boards.add((a, slug))
        elif a == "greenhouse" and jid:
            # The link goes to the company's own site, so guess the board name from it. Wrong guesses just return 404.
            base = guess(x["url"])
            boards |= {("greenhouse", base), ("greenhouse", base.replace("-", ""))} if base else set()
        if not x.get("active") or not x.get("is_visible", True) or ats.is_blocked(x["url"]):
            continue
        jobs.append(RawJob(
            key=ats.job_key(x["url"]), source="simplify", ats=a, company=x["company_name"],
            title=x["title"].strip(), locations=x["locations"], url=x["url"],
            apply_url=ats.apply_url(a, slug, jid, x["url"]),
            posted_at=datetime.fromtimestamp(x["date_posted"], timezone.utc).date().isoformat()))
    return jobs, boards
