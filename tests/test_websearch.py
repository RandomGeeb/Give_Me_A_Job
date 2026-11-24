from datetime import date

import httpx
import pytest

from jobbot import costs, db
from jobbot.llm import client
from jobbot.llm.schemas import FoundRole, FoundRoles
from jobbot.sources import ats, discover, pages, websearch

LISTING = """<html><body><h1>Globex early careers</h1><a href="/jobs/1">Project Management Intern</a> High Wycombe
<a href="/jobs/2">PMO Placement</a> <a href="https://www.linkedin.com/jobs/9">Project Intern</a> <a href="/about">About us</a> """ + "words " * 80 + "</body></html>"
POSTING = "<html><body><h1>Project Delivery Intern at Initech</h1><p>High Wycombe, England. Apply by 2030-01-01.</p>" + "Deliver projects. " * 40 + "</body></html>"


def role(**k):
    return FoundRole(**{"page": "P1", "link": None, "title": "Project Delivery Intern", "company": "Initech", "locations": ["High Wycombe"],
                        "country": "uk", "still_open": "yes", "deadline": None, **k})


PAGE = {"url": "https://initech.test/job", "text": ats.strip_html(POSTING), "links": {"L1": ("Project Delivery Intern", "https://initech.test/j/1")}, "meta": {}}


def jobs(*rs):
    return websearch.to_jobs(FoundRoles(roles=list(rs)), {"P1": PAGE}, date(2026, 11, 23))


def test_only_real_roles_kept():
    ok = jobs(role())[0]
    assert ok.locations == ["High Wycombe, United Kingdom"] and ok.url == PAGE["url"] and ok.source == "web"
    assert jobs(role(link="L1"))[0].url == "https://initech.test/j/1"
    for bad in (dict(title="Quantum Wizard Intern"), dict(company="Umbrella"), dict(link="L9"), dict(page="P7"), dict(country="other"),
                dict(still_open="no"), dict(deadline="2026-01-01")):
        assert jobs(role(**bad)) == []


def test_places_and_hidden_companies():
    assert websearch.places_in("uk", ["London", "Paris", "Tokyo"]) == ["London"]
    assert websearch.places_in("uk", ["High Wycombe"]) == ["High Wycombe, United Kingdom"] and websearch.places_in("uk", []) == ["United Kingdom"]
    assert websearch.places_in("uk", ["Paris", "Tokyo"]) == ["United Kingdom"]
    assert websearch.places_in("sg", ["Singapore", "London"]) == ["Singapore"] and websearch.places_in("sg", ["One North"]) == ["One North, Singapore"]
    assert jobs(role(country="singapore", locations=["One North"]))[0].locations == ["One North, Singapore"]
    assert "in Singapore in" in websearch.prompt("x", date(2026, 1, 1), "sg") and "in the United Kingdom in" in websearch.prompt("x", date(2026, 1, 1))
    for c in ("Company not shown", "Unknown", "", "Confidential"):
        assert jobs(role(company=c)) == []
    assert websearch.skipped("https://www.iagora.com/work/1")


@pytest.fixture
def web(monkeypatch):
    """A small pretend internet."""
    monkeypatch.setattr(pages, "safe", lambda u: not ats.is_blocked(u))
    pages._robots.clear()
    site = {"https://globex.test/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private"),
            "https://globex.test/careers": (200, "text/html", LISTING), "https://globex.test/private/x": (200, "text/html", POSTING),
            "https://initech.test/robots.txt": (404, "text/html", ""), "https://initech.test/job": (200, "text/html", POSTING),
            "https://closed.test/robots.txt": (403, "text/html", ""), "https://closed.test/job": (200, "text/html", POSTING),
            "https://wall.test/robots.txt": (404, "text/html", ""), "https://wall.test/job": (200, "text/html", "<title>Just a moment...</title>" + POSTING),
            "https://hop.test/robots.txt": (404, "text/html", "")}
    asked = []

    def handler(req):
        asked.append(str(req.url))
        if str(req.url) == "https://hop.test/job":
            return httpx.Response(302, headers={"location": "https://www.linkedin.com/jobs/1"})
        s, t, b = site.get(str(req.url), (404, "text/html", ""))
        return httpx.Response(s, headers={"content-type": t}, text=b)
    monkeypatch.setattr(websearch, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return asked


def test_site_rules_respected(web):
    with websearch.client() as c:
        assert pages.get_html(c, "https://globex.test/careers")[1]
        for u in ("https://globex.test/private/x", "https://closed.test/job", "https://wall.test/job", "https://hop.test/job"):
            assert pages.get_html(c, u) == ("", "")
            assert pages.rendered(c, u) == ""       # and the browser fallback must not go round it
    assert "https://globex.test/private/x" not in web and "https://closed.test/job" not in web and not any("linkedin" in u for u in web)


def test_links_come_from_page(web):
    with websearch.client() as c:
        p = websearch.read(c, "https://globex.test/careers")
    assert [v[1] for v in p["links"].values()] == ["https://globex.test/jobs/1", "https://globex.test/jobs/2"]


@pytest.fixture
def claude(monkeypatch):
    """Stands in for the two client functions and records calls as the real ones do."""
    urls = ["https://globex.test/careers", "https://initech.test/job", "https://closed.test/job", "https://www.linkedin.com/jobs/1"]
    asked = []

    def record(purpose):
        with db.connect() as con:
            costs.check(con)
            costs.record(con, purpose, None, "m", {})

    def search(prompt, system, n):
        record("web_search")
        asked.append(prompt)
        return [{"url": u, "title": "t"} for u in urls]

    def call(purpose, system, shared, body, out, **k):
        record(purpose)
        rs = [role(page="P2")]
        if "globex" in body:
            rs += [role(page="P1", link="L1", title="Project Management Intern", company="Globex"), role(page="P1", link="L2", title="PMO Placement", company="Globex")]
        return FoundRoles(roles=rs)
    monkeypatch.setattr(client, "web_search", search)
    monkeypatch.setattr(client, "call", call)
    return asked


def test_whole_run(web, claude):
    with db.connect() as con:
        db.put_setting(con, "families", ["project"])
        db.put_setting(con, "regions", ["uk"])
    res = discover.run_web()
    with db.connect() as con:
        got = {(r["company"], r["title"], r["status"], r["source"]) for r in con.execute("SELECT * FROM jobs")}
    assert got == {("Globex", "Project Management Intern", "discovered", "web"), ("Globex", "PMO Placement", "discovered", "web"),
                   ("Initech", "Project Delivery Intern", "discovered", "web")}
    assert res["pages"] == 2 and res["refused"] == 1 and res["calls"] == 3 and not res["stopped"]
    assert not any("linkedin" in u for u in web)


def test_run_cap_and_daily_limit(web, claude):
    with db.connect() as con:
        db.put_setting(con, "web_search_calls", 4)
    res = discover.run_web()
    assert res["stopped"] and len(claude) == 2 and res["calls"] == 3
    with db.connect() as con:
        db.put_setting(con, "daily_calls", 3)
    with pytest.raises(costs.LimitReached):
        discover.run_web()
    assert len(claude) == 2 and "Daily limit" in discover.state["errors"][0] and not discover.state["running"]


def test_failure_midway_keeps_results(web, monkeypatch):
    calls = []

    def search(*a):
        calls.append(a)
        if len(calls) == 2:
            raise client.LLMError("boom")
        return [{"url": "https://initech.test/job", "title": "t"}]
    monkeypatch.setattr(client, "web_search", search)
    monkeypatch.setattr(client, "call", lambda *a, **k: FoundRoles(roles=[role()]))
    res = discover.run_web()
    assert res["kept"] == 1 and res["stopped"] and discover.state["errors"] == ["boom"]


def test_scarcest_families_first(web, claude):
    from jobbot.models import RawJob, Status
    with db.connect() as con:
        for n, f in enumerate(["software", "data_ml_quant", "product", "analyst"]):
            db.upsert_job(con, RawJob(key=f"k{n}", source="t", company=f"C{n}", title="x", url="https://x.test", locations=["London, UK", "Singapore"]), Status.discovered, None, f)
    discover.run_web()
    assert "project management" in claude[0]
