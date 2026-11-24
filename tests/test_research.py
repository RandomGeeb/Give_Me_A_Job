import sqlite3

import httpx
import pytest

from jobbot import config, db, research
from jobbot.cv import verify
from jobbot.llm import client
from jobbot.llm.schemas import CompanyFindings, Finding
from jobbot.sources import ats, pages
from test_writing import CV, PROFILE

JOB = {"id": 1, "company": "Globex Ltd", "title": "Software Intern", "locations": ["London, UK"], "description": "We use Python.", "family": "software"}
QUOTE = "We moved our billing service to Rust in 2026 because the old one was slow."
PAD = "<p>" + "Globex writes about how its systems work. " * 20 + "</p>"
F = lambda **k: Finding(**{"page": "P1", "quote": QUOTE, "topic": "billing", "published": "2026", **k})
R = [{"quote": QUOTE}]


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(pages, "safe", lambda u: not ats.is_blocked(u))
    pages._robots.clear()
    asked = []
    html = {"https://news.test/a": PAD + "<p>Globex news.</p>", "https://globex.test/blog": f"<nav>menu</nav>{PAD}<p>We moved our billing service to <b>Rust</b> in 2026 because the old one was slow.</p>",
            "https://globex.test/private": PAD, "https://other.test/x": "<p>" + "Different organisation entirely. " * 30 + "</p>"}

    def handler(req):
        u = str(req.url)
        asked.append(u)
        if u.endswith("/robots.txt"):
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200 if u in html else 404, headers={"content-type": "text/html"}, text=html.get(u, ""))
    monkeypatch.setattr(research, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return asked


@pytest.fixture
def claude(monkeypatch):
    sent = []
    links = [{"url": u, "title": "t"} for u in ("https://news.test/a", "https://other.test/x", "https://globex.test/private", "https://www.linkedin.com/company/globex",
                                               "https://job-boards.greenhouse.io/globex/jobs/1", "https://globex.test/blog")]
    monkeypatch.setattr(client, "web_search", lambda *a, **k: sent.append(str(a)) or links)
    monkeypatch.setattr(client, "call", lambda *a, **k: sent.append(str(a)) or CompanyFindings(findings=[F(), F(quote="Globex invented teleportation and everyone in the world now uses it.")]))
    return sent


def test_whole_run(web, claude):
    got = research.gather(JOB)
    assert [(f["quote"], f["url"], f["own_site"], f["published"]) for f in got] == [(QUOTE, "https://globex.test/blog", True, "2026")]
    assert not any(s in u for u in web for s in ("linkedin", "greenhouse", "private"))
    # Only the employer is sent, never the candidate.
    assert len(claude) == 2 and not any(s in x for x in claude for s in ("Sam", "Acme", "Test University", "candidate_facts"))
    n = len(web)
    assert research.gather({**JOB, "id": 2}) == got and len(claude) == 2 and len(web) == n      # kept for next time
    research.gather(JOB, fresh=True)
    assert len(claude) == 4


def test_nothing_found_is_not_kept(web, monkeypatch):
    monkeypatch.setattr(client, "web_search", lambda *a, **k: [{"url": "https://globex.test/missing", "title": ""}])
    assert research.gather(JOB) == []
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM research").fetchone()[0] == 0


def test_only_word_for_word():
    by = {"P1": {"url": "https://globex.test/b", "title": "", "text": "Intro. " + QUOTE.replace(" ", "\n ", 3) + " End."}}
    keep = lambda *fs: research.keep(CompanyFindings(findings=list(fs)), by, "Globex")
    assert len(keep(F(), F())) == 1 and keep(F(page="P9")) == [] and keep(F(quote=QUOTE.replace("Rust", "Go"))) == []
    assert keep(F(quote="our billing service")) == [] and keep(F(published="2025"))[0]["published"] == ""


def test_company_facts_allowed_but_not_claimed():
    check = lambda t, notes="": [i["severity"] for i in verify.check_letter(t, CV, PROFILE, JOB, R, notes)]
    assert check("Globex moved billing to Rust in 2026. I helped build a Python service at Acme.") == ["warn"]      # Rust is theirs, not yours
    assert "block" in check("Globex moved billing to Haskell.") and "block" in check("Globex has 9,000 staff.")
    assert len(check("In 2026 Globex rebuilt billing. I cut latency in 2026.")) == 0      # 2026 is in the CV too
    assert "warn" in [i["severity"] for i in verify.check_letter("I served 55 teams.", CV, PROFILE, {**JOB, "description": "We have 55 teams."}, R)]


@pytest.mark.parametrize("text", ["I have long admired Globex.", "I read your post on billing.", "I've been following your work.", "As a regular user of Globex, I care.", "I met with your team."])
def test_invented_contact_blocked(text):
    assert any("not told the app" in i["problem"] for i in verify.check_letter(text, CV, PROFILE, JOB, R))


def test_true_reasons_allowed():
    assert verify.check_letter("I read your post on billing.", CV, PROFILE, JOB, R, "I read their post on billing last week") == []
    assert verify.check_letter("I used user feedback to fix faults, as a way to help the customer.", CV, PROFILE, JOB, R) == []


def test_company_names():
    assert research.names("JP Morgan Chase") == ["jpmorganchase", "jpmorgan"] and research.names("Wise Ltd") == ["wise"] and research.names("The Trade Desk")[-1] == "thetrade"
    assert research.own_site("https://careers.jpmorgan.com/x", "JP Morgan Chase") and not research.own_site("https://news.test", "Wise")


def test_older_database_upgraded():
    config.ensure_dirs()
    con = sqlite3.connect(config.DB_PATH)
    con.execute("CREATE TABLE applications(job_id INTEGER PRIMARY KEY, cv TEXT, cover_letter TEXT, answers TEXT, review TEXT, pdf_path TEXT, approved_hash TEXT, updated_at TEXT)")
    con.execute("INSERT INTO applications(job_id, cover_letter) VALUES(1, 'old')")
    con.commit()
    con.close()
    with db.connect() as c:
        a = db.get_application(c, 1)
    assert a["cover_letter"] == "old" and a["research"] is None and a["letter_prev"] is None
