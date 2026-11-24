"""Read a job description from a public page's schema.org JobPosting metadata."""
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .. import config
from .ats import is_blocked, strip_html
from .http import get_json

CHALLENGE = re.compile(r"just a moment|checking your browser|cf-challenge|captcha-delivery|verify you are human", re.I)


def safe(url: str) -> bool:
    """Listing URLs come from third parties, so never let one reach this machine or its network."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or is_blocked(url):
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


def _postings(x):
    if isinstance(x, list):
        for i in x:
            yield from _postings(i)
    elif isinstance(x, dict):
        t = x.get("@type")
        if t == "JobPosting" or isinstance(t, list) and "JobPosting" in t:
            yield x
        yield from _postings(x.get("@graph", []))


def parse(html: str) -> dict:
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            for p in _postings(json.loads(m[1], strict=False)):
                return {"description": strip_html(p.get("description") or ""), "title": p.get("title") or "",
                        "company": (p.get("hiringOrganization") or {}).get("name", "") if isinstance(p.get("hiringOrganization"), dict) else "",
                        "deadline": (p.get("validThrough") or "")[:10] or None}
        except ValueError:
            continue
    return {}


def vendor_api(c: httpx.Client, url: str) -> str:
    """Public job APIs for systems whose pages are built by script."""
    u = urlparse(url)
    if not safe(url):
        return ""
    if u.hostname == "apply.workable.com" and (m := re.search(r"/([^/]+)/j/(\w+)", u.path)):
        d = get_json(c, f"https://apply.workable.com/api/v2/accounts/{m[1]}/jobs/{m[2]}")
        return "\n\n".join(strip_html(d.get(k) or "") for k in ("description", "requirements", "benefits")).strip()
    if u.hostname == "jobs.smartrecruiters.com" and (m := re.match(r"/([^/]+)/(\d+)", u.path)):
        d = get_json(c, f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")
        return "\n\n".join(strip_html(x.get("text") or "") for x in d["jobAd"]["sections"].values() if isinstance(x, dict)).strip()
    if u.hostname.endswith(".oraclecloud.com") and (m := re.search(r"/sites/([^/]+)/(?:.*/)?job/(\d+)", u.path)):
        d = get_json(c, f"https://{u.hostname}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                        f"&finder=ById;Id=%22{m[2]}%22,siteNumber={m[1]}")
        x = (d.get("items") or [{}])[0]
        return "\n\n".join(strip_html(x.get(k) or "") for k in (
            "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")).strip()
    return ""


def rendered(c: httpx.Client, url: str) -> str:
    """Last resort for script-built pages: the visible text, read in a throwaway browser with writes blocked.
    Only where the site answers a plain request, so this never gets round a bot block."""
    from playwright.sync_api import sync_playwright

    from ..apply.fill import read_only

    def route(r):
        # Third-party pages must not reach this machine or its network, even by redirect.
        read_only(r) if safe(r.request.url) or r.request.url.startswith(("data:", "blob:")) else r.abort()
    if not get_html(c, url)[1]:
        return ""
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            ctx = b.new_context(service_workers="block")
            ctx.route("**/*", route)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            text = page.evaluate("() => (document.querySelector('main, [role=main], article') || document.body).innerText")
        finally:
            b.close()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    job_like = len(re.findall(r"intern|responsib|require|experience|skills|apply|degree|team", text, re.I)) >= 6
    return text[:20000] if len(text) > 700 and job_like else ""


_robots: dict[str, RobotFileParser | None] = {}


def robots_allow(c: httpx.Client, url: str) -> bool:
    """None in the cache means the site refuses automated readers altogether."""
    u = urlparse(url)
    base = f"{u.scheme}://{u.netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        try:
            r = c.get(base + "/robots.txt", follow_redirects=False)
            if r.status_code in (401, 403, 429) or r.status_code >= 500:
                rp = None
            else:
                plain = r.status_code == 200 and "html" not in r.headers.get("content-type", "")
                rp.parse(r.text.splitlines() if plain else [])
        except httpx.HTTPError:
            rp = None
        _robots[base] = rp
    rp = _robots[base]
    return rp is not None and rp.can_fetch(config.USER_AGENT, url)


def get_html(c: httpx.Client, url: str) -> tuple[str, str]:
    """(final url, html), or ('', '') when the site does not allow or does not serve it."""
    for _ in range(5):
        if not safe(url) or not robots_allow(c, url):
            return "", ""
        with c.stream("GET", url, follow_redirects=False) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return "", ""
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > 4_000_000:
                    return "", ""
            html = body.decode(r.encoding or "utf-8", "replace")
            return ("", "") if CHALLENGE.search(html[:6000]) else (url, html)
    return "", ""


def fetch(c: httpx.Client, url: str) -> dict:
    return parse(get_html(c, url)[1])
