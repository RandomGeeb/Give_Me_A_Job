"""The whole path through the API with the model and browser replaced by stand-ins."""
import time
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from jobbot import api, db, pipeline, research, review, scoring
from jobbot.apply import session as sess
from jobbot.cv import tailor
from jobbot.sources import pages
from test_writing import CV, tcv

H = {"host": "127.0.0.1:8765", "x-jobbot": "1", "origin": "http://127.0.0.1:8765"}


@pytest.fixture
def c():
    pipeline.tasks.clear()
    return TestClient(api.app, headers=H)


def wait(i):
    for _ in range(200):
        t = pipeline.tasks.get(i)
        if t and not t["running"]:
            return t
        time.sleep(.05)
    raise AssertionError("task did not finish")


SCORE = {"is_relevant_internship": True, "family": "software", "eligible": "yes", "eligibility_notes": [], "dates": "fit", "competition": "normal", "deadline": "2027-01-31",
         "application_limit": None, "summary": "Good", "requirements": [{"requirement": "Python", "essential": True, "met": "yes", "evidence": "e2.b1"}]}
GOOD = {"employer_restricts_ai": False, "ai_rule_quote": "", "issues": [], "strengths": ["x"]}
FIELDS = [{"kind": "file", "label": "Resume/CV", "options": [], "required": True, "jb": "jb7", "frame": 0},
          {"kind": "text", "label": "First Name", "options": [], "required": True, "jb": "jb0", "frame": 0},
          {"kind": "textarea", "label": "Why us?", "options": [], "required": True, "jb": "jb1", "frame": 0},
          {"kind": "checkbox", "label": "I agree to the terms", "options": [{"label": "I agree", "jb": "jb2"}], "required": True, "frame": 0}]

FOUND = [{"id": "r1", "quote": "We moved our billing service to Rust in 2026 to cut latency.", "topic": "billing", "url": "https://globex.test/blog", "title": "t", "published": "2026", "own_site": True},
         {"id": "r2", "quote": "Our second post is about something else entirely here.", "topic": "other", "url": "https://globex.test/b", "title": "t", "published": "", "own_site": True}]
WEAK = {"severity": "warn", "where": "Cover letter, opening", "problem": "generic", "suggestion": "", "by": "AI"}


def make(c, url="https://job-boards.greenhouse.io/globex/jobs/1", title="Software Engineer Intern"):
    c.put("/api/cv", json={"cv": CV, "confirmed": True})
    return c.post("/api/jobs", json={"url": url, "company": "Globex", "title": title, "description": "We use Python. " * 30}).json()["id"]


@pytest.fixture
def stand_ins(monkeypatch):
    from jobbot.apply.fields import add_keys
    m = mock.Mock()
    m.tailor.side_effect = lambda *a: tcv("Helped build a Python service")
    m.answers.side_effect = lambda job, cv, profile, fs, *a: {f["key"]: {"value": ["I like Python."], "basis": "drafted", "note": ""} for f in fs}
    m.review.side_effect = lambda *a: {**GOOD, "issues": []}
    monkeypatch.setattr(scoring, "score_job", lambda *a: dict(SCORE))
    monkeypatch.setattr(tailor, "tailor", m.tailor)
    monkeypatch.setattr(tailor, "draft_answers", m.answers)
    m.gather.side_effect = lambda *a: [dict(f) for f in FOUND]
    m.letter.side_effect = lambda *a: ("Globex moved its billing to Rust. I helped build a Python service at Acme.", ["r1"])
    monkeypatch.setattr(research, "gather", m.gather)
    monkeypatch.setattr(tailor, "cover_letter", m.letter)
    monkeypatch.setattr(review, "ai_review", m.review)
    monkeypatch.setattr(sess, "read_form", lambda url: add_keys([dict(f) for f in FIELDS]))
    s = mock.Mock()
    s.extract.side_effect = lambda i: add_keys([dict(f) for f in FIELDS])
    s.fill.return_value = {"filled": 2, "needs_you": ["I agree to the terms"], "failed": []}
    monkeypatch.setattr(sess, "session", s)
    m.session = s
    return m


def to_review(c):
    i = make(c)
    c.post("/api/score", json={"ids": [i]}); assert wait(i)["error"] is None
    c.post(f"/api/jobs/{i}/move", json={"to": "shortlisted"})
    c.post(f"/api/jobs/{i}/tailor"); assert wait(i)["error"] is None
    return i


def test_end_to_end(c, stand_ins):
    i = to_review(c)
    j = c.get(f"/api/jobs/{i}").json()
    assert j["status"] == "needs_review" and j["band"] == "Strong" and j["deadline"] == "2027-01-31" and j["application"]["has_pdf"]
    assert c.post(f"/api/jobs/{i}/open").status_code == 409 and c.post(f"/api/jobs/{i}/fill").status_code == 409
    assert c.post(f"/api/jobs/{i}/approve", json={}).status_code == 200
    assert c.post(f"/api/jobs/{i}/open").status_code == 200
    r = c.post(f"/api/jobs/{i}/fill").json()
    assert r["filled"] == 2 and r["new_questions"] == 0
    sent = stand_ins.session.fill.call_args.args[2]
    assert sent["why us|textarea"]["value"] == ["I like Python."] and sent["i agree to the terms|checkbox"]["value"] == []
    for s in ("submitted", "interview", "offer"):
        assert c.post(f"/api/jobs/{i}/move", json={"to": s}).status_code == 200
    ins = c.get("/api/insights").json()
    assert ins["likelihood"]["bands"]["Strong"]["interview"]["hits"] == 1 and ins["likelihood"]["basis"] == "assumption-based"
    assert {f["stage"]: f["n"] for f in ins["funnel"]}["offer"] == 1


def test_edit_cancels_approval(c, stand_ins):
    i = to_review(c)
    c.post(f"/api/jobs/{i}/approve", json={})
    assert c.put(f"/api/jobs/{i}/application", json={"answers": {"why us|textarea": ["Changed"]}}).status_code == 200
    j = c.get(f"/api/jobs/{i}").json()
    assert j["status"] == "needs_review" and not j["application"]["approved"] and j["application"]["review"]["ai_stale"]
    assert c.post(f"/api/jobs/{i}/open").status_code == 409


def test_consent_cannot_be_edited_in(c, stand_ins):
    i = to_review(c)
    c.put(f"/api/jobs/{i}/application", json={"answers": {"i agree to the terms|checkbox": ["I agree"]}})
    assert c.get(f"/api/jobs/{i}").json()["application"]["answers"]["i agree to the terms|checkbox"]["value"] == []


def test_false_claim_revised_then_blocks(c, stand_ins):
    stand_ins.tailor.side_effect = lambda *a: tcv("Led a Kubernetes platform serving 9,999 users")
    i = to_review(c)
    assert stand_ins.tailor.call_count == 3 and stand_ins.review.call_count == 1
    r = c.post(f"/api/jobs/{i}/approve", json={})
    assert r.status_code == 409 and "blocking" in r.json()["detail"]
    assert c.post(f"/api/jobs/{i}/approve", json={"override": "checked"}).status_code == 200


def test_ai_rule_blocks(c, stand_ins):
    stand_ins.review.side_effect = lambda *a: {**GOOD, "issues": [{"severity": "block", "where": "Employer rule", "by": "AI", "problem": "no AI", "suggestion": ""}]}
    i = to_review(c)
    assert stand_ins.tailor.call_count == 1 and c.post(f"/api/jobs/{i}/approve", json={}).status_code == 409


def test_new_questions_need_approval(c, stand_ins):
    from jobbot.apply.fields import add_keys
    i = to_review(c)
    c.post(f"/api/jobs/{i}/approve", json={}); c.post(f"/api/jobs/{i}/open")
    extra = FIELDS + [{"kind": "textarea", "label": "Page two question", "options": [], "required": True, "jb": "jb9", "frame": 0}]
    stand_ins.session.extract.side_effect = lambda i: add_keys([dict(f) for f in extra])
    r = c.post(f"/api/jobs/{i}/fill").json()
    assert wait(i)["kind"] == "review" and stand_ins.review.call_count == 2
    assert r["new_questions"] == 1 and "page two question|textarea" not in stand_ins.session.fill.call_args.args[2]
    assert c.get(f"/api/jobs/{i}").json()["status"] == "needs_review" and c.post(f"/api/jobs/{i}/fill").status_code == 409
    c.post(f"/api/jobs/{i}/approve", json={})
    assert c.post(f"/api/jobs/{i}/open").status_code == 200
    c.post(f"/api/jobs/{i}/fill")
    assert stand_ins.session.fill.call_args.args[2]["page two question|textarea"]["value"]


def test_mislabelled_draft_still_checked(c, stand_ins):
    stand_ins.answers.side_effect = lambda job, cv, profile, fs, *a: {f["key"]: {"value": ["I grew revenue by 73% at Initech."], "basis": "profile", "note": ""} for f in fs}
    i = to_review(c)
    assert c.get(f"/api/jobs/{i}").json()["application"]["blockers"] == 1


def test_cover_letter_attached(c, stand_ins, monkeypatch):
    from jobbot.apply.fields import add_keys
    form = FIELDS + [{"kind": "file", "label": "Cover Letter", "options": [], "required": False, "jb": "jb8", "frame": 0}]
    monkeypatch.setattr(sess, "read_form", lambda url: add_keys([dict(f) for f in form]))
    stand_ins.session.extract.side_effect = lambda i: add_keys([dict(f) for f in form])
    i = to_review(c)
    c.post(f"/api/jobs/{i}/approve", json={}); c.post(f"/api/jobs/{i}/open"); c.post(f"/api/jobs/{i}/fill")
    path = stand_ins.session.fill.call_args.args[3]["cover_letter"]
    assert path.name == "Sam_Test_Cover_Letter.pdf" and path.read_bytes().startswith(b"%PDF")


def test_newsletter_is_not_an_application(c, stand_ins, monkeypatch):
    from jobbot.apply.fields import add_keys
    monkeypatch.setattr(sess, "read_form", lambda url: add_keys([
        {"kind": "text", "label": "Get industry insights and updates", "options": [], "required": False, "jb": "jb0", "frame": 0},
        {"kind": "text", "label": "Postcode", "options": [], "required": False, "jb": "jb1", "frame": 0}]))
    i = to_review(c)
    a = c.get(f"/api/jobs/{i}").json()["application"]
    assert a["answers"] == {} and any("could not be read" in x["problem"] for x in a["review"]["issues"])


def test_company_cap(c, stand_ins):
    ids = []
    for n in range(3):
        i = make(c, f"https://job-boards.greenhouse.io/globex/jobs/{n}", f"Software Intern {n}")
        c.post(f"/api/jobs/{i}/move", json={"to": "shortlisted"}); c.post(f"/api/jobs/{i}/tailor"); wait(i)
        ids.append(c.post(f"/api/jobs/{i}/approve", json={}).status_code)
    assert ids == [200, 200, 409]


def test_failure_returns_to_shortlist(c, stand_ins):
    stand_ins.tailor.side_effect = pipeline.LLMError("boom")
    i = make(c); c.post(f"/api/jobs/{i}/move", json={"to": "shortlisted"}); c.post(f"/api/jobs/{i}/tailor")
    assert wait(i)["error"] == "boom" and c.get(f"/api/jobs/{i}").json()["status"] == "shortlisted"


def test_cannot_skip_review(c, stand_ins):
    i = make(c)
    for s in ("approved", "filling", "needs_review"):
        assert c.post(f"/api/jobs/{i}/move", json={"to": s}).status_code == 409


def test_unconfirmed_cv_refused(c):
    assert c.post("/api/score", json={"ids": [1]}).status_code == 409


def test_restart_recovery(c, stand_ins):
    i = to_review(c)
    c.post(f"/api/jobs/{i}/approve", json={}); c.post(f"/api/jobs/{i}/open")
    pipeline.recover()
    assert c.get(f"/api/jobs/{i}").json()["status"] == "approved"


@pytest.mark.parametrize("h", [{"host": "evil.test"}, {"origin": "https://evil.test"}, {"host": "evil.test:8765"}])
def test_other_sites_refused(c, h):
    assert c.get("/api/jobs", headers=h).status_code == 403


def test_write_needs_header(c):
    assert TestClient(api.app, headers={"host": H["host"]}).post("/api/discover").status_code == 403


def test_adding_a_country_rereads_boards(c):
    c.put("/api/settings", json={"regions": ["uk"]})
    with db.connect() as con:
        con.execute("INSERT INTO boards(ats,slug,polled_at) VALUES('lever','x','2026-01-01')")
    c.put("/api/settings", json={"regions": ["uk"]})
    with db.connect() as con:
        assert con.execute("SELECT polled_at FROM boards").fetchone()["polled_at"]
    c.put("/api/settings", json={"regions": ["uk", "sg"]})
    with db.connect() as con:
        assert con.execute("SELECT polled_at FROM boards").fetchone()["polled_at"] is None
    assert c.put("/api/settings", json={"regions": []}).status_code == 422 and c.put("/api/settings", json={"regions": ["fr"]}).status_code == 422


def test_page_metadata():
    html = '<script type="application/ld+json">{"@graph":[{"@type":"JobPosting","description":"<p>Hello &amp; hi</p>","validThrough":"2027-01-02T00:00"}]}</script>'
    assert pages.parse(html) == {"description": "Hello & hi", "title": "", "company": "", "deadline": "2027-01-02"}
    for u in ("http://127.0.0.1/x", "http://localhost:8765/", "file:///etc/passwd", "https://www.linkedin.com/jobs/1", "http://169.254.169.254/"):
        assert not pages.safe(u)


def letter(c, i, **body):
    c.post(f"/api/jobs/{i}/letter", json=body)
    t = wait(i)
    return t, c.get(f"/api/jobs/{i}").json()


def test_letter_from_research(c, stand_ins):
    i = to_review(c)
    assert stand_ins.gather.call_count == 0          # no research is spent on forms that want no letter
    c.post(f"/api/jobs/{i}/approve", json={})
    before = c.get(f"/api/jobs/{i}").json()["application"]["cv"]
    t, j = letter(c, i, notes="I met two engineers at the careers fair.")
    a = j["application"]
    assert t["error"] is None and j["status"] == "needs_review" and not a["approved"] and a["cv"] == before
    assert [f["used"] for f in a["research"]] == [True, False] and a["letter_notes"].startswith("I met") and not a["blockers"]
    assert c.post(f"/api/jobs/{i}/open").status_code == 409
    assert stand_ins.letter.call_args.args[4].startswith("I met")
    stand_ins.letter.side_effect = lambda *a: ("I helped build a Python service.", [])
    assert letter(c, i)[1]["application"]["letter_prev"].startswith("Globex moved")


def test_weak_letter_rewritten_once(c, stand_ins):
    i = to_review(c)
    stand_ins.review.reset_mock()
    stand_ins.review.side_effect = lambda *a: {**GOOD, "issues": [dict(WEAK)]}
    a = letter(c, i)[1]["application"]
    assert stand_ins.letter.call_count == 2 and stand_ins.letter.call_args.args[6][0]["problem"] == "generic"
    assert stand_ins.review.call_count == 2 and any(x["problem"] == "generic" for x in a["review"]["issues"])     # still shown


def test_weak_letter_rewritten_while_tailoring(c, stand_ins, monkeypatch):
    from jobbot.apply.fields import add_keys
    form = FIELDS + [{"kind": "file", "label": "Cover Letter", "options": [], "required": False, "jb": "jb8", "frame": 0}]
    monkeypatch.setattr(sess, "read_form", lambda url: add_keys([dict(f) for f in form]))
    stand_ins.review.side_effect = [{**GOOD, "issues": [dict(WEAK)]}, {**GOOD, "issues": []}]
    i = to_review(c)
    assert stand_ins.letter.call_count == 2 and stand_ins.tailor.call_count == 1 and stand_ins.gather.call_count == 1
    assert c.get(f"/api/jobs/{i}").json()["application"]["research"][0]["used"]


def test_weak_letter_rewritten_after_a_failed_draft(c, stand_ins):
    i = to_review(c)
    good = ("I helped build a Python service.", [])
    stand_ins.letter.side_effect = [("I know Zebrafish well.", []), good, good]
    stand_ins.review.side_effect = [{**GOOD, "issues": [dict(WEAK)]}, {**GOOD, "issues": []}]
    assert letter(c, i)[1]["application"]["review"]["issues"] == [] and stand_ins.letter.call_count == 3


def test_unsupported_letter_blocks(c, stand_ins):
    i = to_review(c)
    stand_ins.letter.side_effect = lambda *a: ("I have long admired Globex and its work on Zebrafish.", [])
    a = letter(c, i)[1]["application"]
    assert stand_ins.letter.call_count == 3 and a["blockers"] == 2 and c.post(f"/api/jobs/{i}/approve", json={}).status_code == 409


def test_letter_without_research(c, stand_ins):
    from jobbot.llm.client import LLMError
    i = to_review(c)
    stand_ins.letter.side_effect = lambda *a: ("I helped build a Python service.", [])
    for effect in ([[]], LLMError("boom")):
        stand_ins.gather.side_effect = effect
        a = letter(c, i)[1]["application"]
        assert a["cover_letter"] and any(x["where"] == "Company research" for x in a["review"]["issues"])


def test_failed_rewrite_changes_nothing(c, stand_ins):
    i = to_review(c)
    letter(c, i)
    c.post(f"/api/jobs/{i}/approve", json={})
    stand_ins.letter.side_effect = RuntimeError("boom")
    t, j = letter(c, i)
    assert "boom" in t["error"] and j["status"] == "approved" and j["application"]["approved"]


def test_letter_needs_tailoring_first(c, stand_ins):
    i = make(c)
    assert "Tailor the application first" in letter(c, i)[0]["error"]
