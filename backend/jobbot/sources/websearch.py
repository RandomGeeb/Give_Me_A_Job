"""Find roles on the open web. Claude searches; code reads the pages; Claude lists the roles on them.

Every link comes from a search result or from a page, never from the model. Pages are read only where robots.txt and
the site allow it.
"""
import json
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from .. import costs, db
from ..llm import client as llm
from ..llm import prompts
from ..llm.client import LLMError
from ..llm.schemas import FoundRoles
from ..models import RawJob
from .. import filters
from . import ats, pages
from .http import client

TOPICS = {
    "project": ["project management internships and placement years", "programme management, PMO and delivery management internships"],
    "product": ["product management internships"],
    "analyst": ["technology and business analyst internships and placements", "technology consulting internships and IT placement years"],
    "software": ["software engineering internships and placement years"],
    "data_ml_quant": ["data science, machine learning and quantitative developer internships"],
}
# Sites that forbid or block automated readers, or only re-list other people's postings.
SKIP = ["linkedin.com", "indeed.com", "indeed.co.uk", "glassdoor.com", "glassdoor.co.uk", "adzuna.co.uk", "reed.co.uk", "gradcracker.com",
        "brightnetwork.co.uk", "ratemyplacement.co.uk", "higherin.com", "totaljobs.com", "cv-library.co.uk", "monster.co.uk", "jooble.org",
        "ziprecruiter.co.uk", "simplyhired.co.uk", "talent.com", "reddit.com", "youtube.com", "wikipedia.org", "facebook.com", "quora.com",
        "thestudentroom.co.uk", "iagora.com", "prosple.com", "dreamworkhq.com", "avjobs.com", "prospects.ac.uk", "jobstreet.com", "jobsdb.com", "glassdoor.sg", "indeed.com.sg", "mycareersfuture.gov.sg", "foundit.sg", "medium.com"]
SEARCHES_PER_TOPIC, MAX_PAGES, PAGE_CHARS, BATCH = 3, 60, 14000, 5
NO_COMPANY = filters.NO_COMPANY
norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def skipped(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return ats.is_blocked(url) or any(host == d or host.endswith("." + d) for d in SKIP)


def prompt(topic: str, today: date, region: str = "uk") -> str:
    return (f"Find {topic} in {'the ' if region == 'uk' else ''}{filters.REGIONS[region]} in technology teams or technology companies, for {today.year + 1} intakes, open to university "
            f"students and currently accepting applications. Today is {today.isoformat()}. Prefer employers' own careers pages and postings.")


def read(c, url: str) -> dict | None:
    final, html = pages.get_html(c, url)
    if not html or skipped(final):
        return None
    meta = pages.parse(html)
    body = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", html)
    links, n = {}, 0
    for m in re.finditer(r"(?is)<a\b[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", body):
        text, href = ats.strip_html(m[2]), urljoin(final, ats.html.unescape(m[1]))
        near = text + " " + urlparse(href).path
        if href.startswith("http") and not skipped(href) and re.search(r"intern|placement|summer|industrial|analyst|student|graduate", near, re.I):
            n += 1
            links[f"L{n}"] = (re.sub(r"\s+", " ", text)[:160], href)
            if n == 80:
                break
    text = ats.strip_html(body)
    return {"url": final, "text": text, "links": links, "meta": meta}


def places_in(region: str, places: list[str]) -> list[str]:
    """Keep the ones in that country. A lone unrecognised town on a page that names the country is trusted; one in a
    list of cities is not."""
    here = [x for x in places if region in filters.regions([x])]
    if here:
        return here
    return [f"{places[0]}, {filters.REGIONS[region]}"] if len(places) == 1 and places[0].strip() else [filters.REGIONS[region]]


def to_jobs(found: FoundRoles, by: dict[str, dict], today: date) -> list[RawJob]:
    out = []
    for r in found.roles:
        p = by.get(r.page)
        region = {"uk": "uk", "singapore": "sg"}.get(r.country)
        if not p or not region or r.still_open == "no" or NO_COMPANY.search(r.company.strip()):
            continue
        dl = r.deadline if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.deadline or "") else None
        if dl and dl < today.isoformat():
            continue
        if r.link:
            if r.link not in p["links"]:
                continue
            text, url = p["links"][r.link]
        else:
            url = p["url"]
        # The title must really be on the page.
        words = [w for w in norm(r.title).split() if len(w) > 2]
        hay = norm(p["text"] + " " + (p["links"][r.link][0] if r.link else ""))
        if not words or sum(w in hay for w in words) < 0.8 * len(words) or norm(r.company) not in hay + " " + norm(urlparse(p["url"]).netloc):
            continue
        a, slug, jid = ats.parse_url(url)
        locs = places_in(region, r.locations)
        out.append(RawJob(key=ats.job_key(url), source="web", ats=a, company=r.company.strip(), title=r.title.strip(), locations=locs, url=url,
                          apply_url=ats.apply_url(a, slug, jid, url), deadline=dl,
                          description=p["text"][:20000] if not r.link and len(p["text"]) > 400 else ""))
    return out


def run(state: dict) -> dict:
    from .discover import store
    today = date.today()
    with db.connect() as con:
        s = db.settings(con)
        costs.check(con)
        start, cap = con.execute("SELECT COALESCE(MAX(id),0) m FROM usage").fetchone()["m"], s["web_search_calls"]
        known = {r["url"] for r in con.execute("SELECT url FROM jobs")} | {r["key"] for r in con.execute("SELECT key FROM jobs")}
    res = {"seen": 0, "new": 0, "kept": 0, "pages": 0, "refused": 0, "shortened": 0, "calls": 0, "stopped": False}

    def spent() -> int:
        with db.connect() as con:
            res["calls"] = con.execute("SELECT COUNT(*) c FROM usage WHERE id>? AND purpose IN ('web_search','read_pages')", (start,)).fetchone()["c"]
        return res["calls"]

    def keep(jobs):
        with db.connect() as con:
            n = store(con, jobs, s["families"], s["regions"])
        for k in n:
            res[k] += n[k]

    # Country and role type with the fewest open roles first, so a small cap goes where it helps most.
    have = {(g, f): 0 for g in s["regions"] for f in s["families"]}
    with db.connect() as con:
        for r in con.execute("SELECT family, locations FROM jobs WHERE status!='filtered_out'"):
            for g in filters.regions(json.loads(r["locations"] or "[]")):
                if (g, r["family"]) in have:
                    have[g, r["family"]] += 1
    topics = [(g, t) for g, f in sorted(have, key=have.get) for t in TOPICS[f]]
    hits: dict[str, dict] = {}
    state.update(phase="web search", total=len(topics), done=0)
    for g, t in topics:
        if spent() >= max(1, cap // 2):      # leave the rest for reading what was found
            res["stopped"] = True
            break
        try:
            found = llm.web_search(prompt(t, today, g), prompts.SEARCH, SEARCHES_PER_TOPIC)
        except (LLMError, costs.LimitReached) as e:
            if not hits:
                raise
            state["errors"].append(str(e))      # still read what earlier searches found
            res["stopped"] = True
            break
        for h in found:
            hits.setdefault(h["url"], h)
        state["done"] += 1
    with client() as c:
        todo, boards = [], set()
        for u in hits:
            if skipped(u) or u in known or ats.job_key(u) in known:
                continue
            a, slug, _ = ats.parse_url(u)
            if slug and a in ats.POLL:
                boards.add((a, slug))       # these boards have free, exact listings
            else:
                todo.append(u)
        for a, slug in boards:
            with db.connect() as con:
                con.execute("INSERT OR IGNORE INTO boards(ats,slug) VALUES(?,?)", (a, slug))
            try:
                keep(ats.POLL[a](c, slug))
            except Exception as e:
                state["errors"].append(f"{a} {slug}: {type(e).__name__}")
        state.update(phase="reading pages", total=min(len(todo), MAX_PAGES), done=0)
        batch: dict[str, dict] = {}

        def flush():
            if not batch:
                return
            body = [{"page": i, "address": p["url"], "text": p["text"][:PAGE_CHARS],
                     "links": [{"id": k, "text": v[0]} for k, v in p["links"].items()]} for i, p in batch.items()]
            res["shortened"] += sum(len(p["text"]) > PAGE_CHARS for p in batch.values())
            pages_read = dict(batch)
            batch.clear()
            found = llm.call("read_pages", prompts.FIND, "", llm.tag("job", body) + f"\n\nToday is {today.isoformat()}. List the roles.",
                             FoundRoles, effort="low")
            keep(to_jobs(found, pages_read, today))

        if len(todo) > MAX_PAGES:
            res["stopped"] = True
        for u in todo[:MAX_PAGES]:
            if len(batch) == 0 and spent() >= cap:
                res["stopped"] = True
                break
            try:
                p = read(c, u)
            except Exception:
                p = None
            state["done"] += 1
            if p is None or len(p["text"]) < 200:
                res["refused"] += 1
                continue
            res["pages"] += 1
            batch[f"P{res['pages']}"] = p
            if len(batch) == BATCH:
                try:
                    flush()
                except LLMError as e:          # one bad batch should not lose the rest
                    state["errors"].append(str(e))
        try:
            flush()
        except (LLMError, costs.LimitReached) as e:
            state["errors"].append(str(e))
            res["stopped"] = True
    spent()
    return res
