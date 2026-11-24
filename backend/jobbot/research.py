"""What a company's own pages and the news say about it, for the cover letter.

Claude only chooses what to search for and which passages matter. Code reads the pages, and a passage is kept only if it
appears word for word on the page. Nothing about the candidate is in these calls.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from . import db
from .llm import client as llm
from .llm import prompts
from .llm.schemas import CompanyFindings
from .sources import ats, pages
from .sources.http import client
from .sources.websearch import skipped

SEARCHES, MAX_TRIES, MAX_PAGES, PER_HOST, PAGE_CHARS, KEEP_DAYS = 4, 14, 7, 4, 9000, 30
FOCUS = {"software": "engineering blog posts, systems they built and technical choices they explained",
         "data_ml_quant": "data, machine learning or research work they published, and technical choices they explained",
         "project": "major programmes, launches and how they deliver them",
         "product": "product launches, product decisions and the user problems behind them",
         "analyst": "client work, systems changes and business problems they described"}
words = lambda s: " ".join(re.findall(r"\w+", (s or "").lower()))


def names(company: str) -> list[str]:
    """The full name run together, and any shorter start of it long enough to be telling (JP Morgan Chase: jpmorgan)."""
    ws = re.sub(r"\b(ltd|limited|plc|inc|llc|llp|group|uk|pte|holdings)\b", "", db.norm(company)).split()
    return [n for i in range(len(ws), 0, -1) if len(n := "".join(ws[:i])) > (2 if i == len(ws) else 5)]


def own_site(url: str, company: str) -> bool:
    host = re.sub(r"[^a-z0-9]", "", (urlparse(url).hostname or "").lower().removeprefix("www."))
    return any(n in host for n in names(company))


def prompt(job: dict, today: date) -> str:
    return (f"Company: {job['company']}\nRole: {job['title']}\n\n" + llm.tag("job", (job["description"] or "")[:1500]) +
            f"\n\nToday is {today.isoformat()}. Find {FOCUS.get(job['family'], FOCUS['software'])}, mainly from the last 18 months. "
            "The posting is there so you pick the right organisation if others share its name.")


def read(c, links: list[dict], company: str) -> dict[str, dict]:
    """Pages that the site allows, company's own first."""
    out, hosts, tries = {}, {}, 0
    for x in sorted(links, key=lambda x: not own_site(x["url"], company)):
        host = urlparse(x["url"]).hostname or ""
        if len(out) == MAX_PAGES or tries == MAX_TRIES:
            break
        # A posting on a job board says nothing the advert does not.
        if skipped(x["url"]) or ats.parse_url(x["url"])[0] in ats.POLL or hosts.get(host, 0) >= PER_HOST:
            continue
        tries += 1
        hosts[host] = hosts.get(host, 0) + 1
        try:
            url, html = pages.get_html(c, x["url"])
        except Exception:
            continue
        if not html or skipped(url):
            continue
        body = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer|form)\b.*?</\1>", " ", html)
        text = ats.strip_html(body)
        if len(text) < 400 or not any(n in re.sub(r"[^a-z0-9]", "", (text + host).lower()) for n in names(company)):
            continue
        out[f"P{len(out) + 1}"] = {"url": url, "title": x["title"][:160], "text": text}
    return out


def keep(found: CompanyFindings, by: dict[str, dict], company: str) -> list[dict]:
    out, seen = [], set()
    for f in found.findings:
        p = by.get(f.page)
        q = words(f.quote)
        if not p or q in seen or not 8 <= len(q.split()) <= 90 or f" {q} " not in f" {words(p['text'])} ":
            continue
        seen.add(q)
        year = re.match(r"20\d\d", f.published or "")
        out.append({"id": f"r{len(out) + 1}", "quote": re.sub(r"\s+", " ", f.quote).strip(), "topic": f.topic, "url": p["url"], "title": p["title"],
                    # A date counts only if the page shows that year.
                    "published": f.published if year and year[0] in p["text"] else "", "own_site": own_site(p["url"], company)})
    return out


def gather(job: dict, fresh: bool = False) -> list[dict]:
    """Kept for a month per company and role type, so a second letter there costs nothing extra."""
    key = f"{db.norm(job['company'])}|{job['family'] or ''}"
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    with db.connect() as con:
        r = con.execute("SELECT findings FROM research WHERE k=? AND at>=?", (key, cutoff)).fetchone()
    if r and not fresh:
        return json.loads(r["findings"])
    today = date.today()
    links = llm.web_search(prompt(job, today), prompts.SEARCH_COMPANY, SEARCHES, purpose="company_search", job_id=job["id"])
    with client() as c:
        by = read(c, links, job["company"])
    if not by:
        return []
    body = [{"page": i, "address": p["url"], "text": p["text"][:PAGE_CHARS]} for i, p in by.items()]
    found = llm.call("company_research", prompts.RESEARCH, "", f"Company: {job['company']}\nRole: {job['title']}\n\n" + llm.tag("job", body) +
                     f"\n\nToday is {today.isoformat()}. Pick the passages.", CompanyFindings, effort="medium", job_id=job["id"])
    out = keep(found, by, job["company"])
    if out:
        with db.connect() as con:
            con.execute("INSERT INTO research VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET at=excluded.at, findings=excluded.findings",
                        (key, db.now(), json.dumps(out)))
    return out
