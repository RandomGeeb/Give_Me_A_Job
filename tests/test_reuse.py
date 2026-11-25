import copy

import pytest

from jobbot import auto, config, db, pipeline
from jobbot.cv import render, reuse, tailor
from jobbot.llm import client, schemas as s
from jobbot.models import RawJob, Status

CV = {"name": "Sam Test", "email": "s@x.test", "phone": "", "links": [], "skills": [{"group": "Languages", "items": ["Python", "SQL", "Docker", "Go"]}], "entries": [
    {"id": "e1", "kind": "education", "title": "BSc Computing", "organisation": "Leeds", "location": "", "dates": "2024-2027", "bullets": []},
    {"id": "e2", "kind": "experience", "title": "Intern", "organisation": "Acme", "location": "", "dates": "2025", "bullets": [
        {"id": "e2.b1", "text": "Built Python pipelines loading market prices into warehouse tables"},
        {"id": "e2.b2", "text": "Containerised services with Docker and monitored deployment failures"}]}]}
TCV = {"entries": [{"entry_id": "e1", "bullets": []}, {"entry_id": "e2", "bullets": [{"text": CV["entries"][1]["bullets"][0]["text"], "fact_ids": ["e2.b1"]}]}],
       "skills": [{"group": "Languages", "items": ["Python", "SQL"]}], "changes": ["x"]}
DATA = "You will build Python pipelines, loading market prices into our warehouse tables with SQL. " * 8
OPS = "You will run Docker and Go, containerised services, monitored deployment, failures and Python at scale. " * 8


def add(company, title, desc, n=[0], family="software", city="London, UK", status=Status.discovered):
    n[0] += 1
    with db.connect() as con:
        return db.upsert_job(con, RawJob(key=f"r{n[0]}", source="t", company=company, title=title, locations=[city], url=f"https://x.test/r{n[0]}", description=desc), status, None, family)[0]


def finished(i, t=TCV, cv=CV, issues=()):
    with db.connect() as con:
        con.execute("UPDATE jobs SET status='needs_review' WHERE id=?", (i,))
        db.save_application(con, i, cv=t, review={"issues": list(issues)}, cv_from={"rev": reuse.rev(cv)})


@pytest.fixture(autouse=True)
def corpus():
    pipeline.tasks.clear()
    with db.connect() as con:
        db.put_setting(con, "cv", CV)
        db.put_setting(con, "cv_confirmed", True)
    for k in range(6):
        add(f"Other{k}", "Gardener", "Plants and soil all day long, with watering and weeding. " * 10, family="other")


def look(i, cv=CV):
    with db.connect() as con:
        return reuse.find(con, db.get_job(con, i), cv)


def test_finds_close_match_only():
    a = add("Acme Data", "Data Intern", DATA)
    finished(a)
    hit = look(add("Globex", "Engineer Intern", DATA + " Join us in Leeds."))
    assert hit["job_id"] == a and hit["match"] > .9
    assert look(add("Initech", "Platform Intern", OPS)) is None                          # asks for what that CV leaves out
    assert look(add("Globex2", "Data Intern", DATA, family="product")) is None           # another kind of role
    assert look(add("Globex3", "Data Intern", "Great culture. " * 40)) is None           # nothing to compare


def test_never_reuses_bad_or_outdated_cvs():
    a = add("Acme Data", "Data Intern", DATA)
    b = add("Globex", "Engineer Intern", DATA)
    finished(a, issues=[{"severity": "block", "where": "CV: Acme", "problem": "x"}])
    assert look(b) is None
    bad = copy.deepcopy(TCV)
    bad["entries"][1]["bullets"][0]["text"] = "Built Kubernetes pipelines for 9,999 users"
    finished(a, bad)
    assert look(b) is None
    finished(a)
    newer = copy.deepcopy(CV)
    newer["entries"][1]["bullets"].append({"id": "e2.b3", "text": "Taught Python workshops"})
    assert look(b, newer) is None and look(b)
    with db.connect() as con:
        db.put_setting(con, "reuse_percent", 0)
    assert look(b) is None


@pytest.fixture
def writing(monkeypatch):
    calls, fit = [], []

    def call(purpose, system, shared, content, out, **k):
        calls.append(purpose)
        if purpose == "tailor":
            return s.TailoredCV(**TCV)
        return s.Review(employer_restricts_ai=False, ai_rule_quote="", strengths=[], issues=[s.Issue(**fit.pop())] if fit else [])
    monkeypatch.setattr(client, "call", call)
    monkeypatch.setattr(render, "render", lambda t, cv, p, i: (config.DATA / "x.pdf", 1))
    return calls, fit


def run(i):
    with db.connect() as con:
        if db.get_job(con, i)["status"] == "discovered":
            db.set_status(con, i, "shortlisted")
    pipeline.tailor_job(i)
    with db.connect() as con:
        return db.get_application(con, i)


def test_reuse_skips_tailoring_but_not_review(writing):
    calls, fit = writing
    a, b = add("Acme Data", "Data Intern", DATA), add("Globex", "Engineer Intern", DATA)
    run(a)
    assert calls == ["tailor", "review"]
    calls.clear()
    app = run(b)
    assert calls == ["review"] and app["cv_from"]["job_id"] == a and "Acme Data" in app["cv"]["changes"][0] and not app["approved_hash"]
    calls.clear()
    assert "job_id" not in run(b)["cv_from"] and calls == ["tailor", "review"]           # asking again writes a new one


def test_reviewer_can_send_a_reused_cv_back(writing):
    calls, fit = writing
    run(add("Acme Data", "Data Intern", DATA))
    calls.clear()
    fit.append({"severity": "warn", "where": "CV fit", "problem": "Docker is essential and missing.", "suggestion": "Add e2.b2."})
    app = run(add("Globex", "Engineer Intern", DATA))
    assert calls == ["review", "tailor", "review"] and app["cv_from"]["changed"]


def test_too_long_is_trimmed_without_a_model(writing, monkeypatch):
    long = copy.deepcopy(TCV)
    long["entries"][1]["bullets"] = [{"text": f"{CV['entries'][1]['bullets'][0]['text']}{'.' * k}", "fact_ids": ["e2.b1"]} for k in range(5)]
    monkeypatch.setattr(tailor, "tailor", lambda *a: long)
    monkeypatch.setattr(render, "render", lambda t, cv, p, i: (config.DATA / "x.pdf", 1 if sum(len(e["bullets"]) for e in t["entries"]) <= 3 else 2))
    app = run(add("Acme Data", "Data Intern", DATA))
    assert len(app["cv"]["entries"][1]["bullets"]) == 3 and writing[0] == ["review"] and app["review"]["revisions"] == 0
    assert sum(c.startswith("Left out to fit") for c in app["cv"]["changes"]) == 2


def test_same_role_listed_twice():
    a = add("Graphcore", "Cloud Intern", DATA)
    finished(a)
    b, c = add("Graphcore Early Careers", "Cloud Intern", DATA), add("Graphcore Early Careers", "Cloud Intern", DATA, city="Leeds, UK")
    with db.connect() as con:
        con.execute("UPDATE jobs SET tier=1 WHERE id IN (?,?)", (b, c))
        db.put_setting(con, "max_per_company", 5)          # both names are one employer, so they share its cap
        queue, _, _, twins = auto.candidates(con)
        assert [q["id"] for q in queue] == [c] and [t["id"] for t in twins] == [b] and twins[0]["twin"] == "Graphcore"
        db.set_status(con, b, "shortlisted")
        assert {q["id"] for q in auto.candidates(con)[0]} == {b, c}
