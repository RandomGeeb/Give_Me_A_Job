import copy

import pytest

from jobbot import review, scoring
from jobbot.cv import parse, render, tailor, verify
from jobbot.llm import client
from jobbot.config import DEFAULT_SETTINGS

PROFILE = {**copy.deepcopy(DEFAULT_SETTINGS["profile"]), "first_name": "Sam", "last_name": "Test", "email": "s@x.test",
           "sensitive": {"gender": "SECRET-GENDER"}}
CV = parse.with_ids({"name": "Sam Test", "email": "s@x.test", "phone": "", "links": [], "entries": [
    {"kind": "education", "title": "BSc Computer Science", "organisation": "Test University", "location": "London", "dates": "2025-2028", "bullets": ["First class average"]},
    {"kind": "experience", "title": "Software Intern", "organisation": "Acme", "location": "London", "dates": "2026",
     "bullets": ["Helped build a Python service handling 1,200 requests per day", "Wrote tests with pytest"]},
], "skills": [{"group": "Languages", "items": ["Python", "SQL"]}]})
JOB = {"id": 1, "company": "Globex", "title": "Software Intern", "locations": ["London, UK"], "description": "We use Kubernetes and Python.", "posted_at": None}


def tcv(text, ids=("e2.b1",), skills=("Python",)):
    return {"entries": [{"entry_id": "e1", "bullets": []}, {"entry_id": "e2", "bullets": [{"text": text, "fact_ids": list(ids)}]}],
            "skills": [{"group": "Languages", "items": list(skills)}], "changes": []}


@pytest.mark.parametrize("text", [
    "Helped build a Python service handling 1,200 requests per day",
    "Helped build a service in Python for 1200 daily requests", "Contributed to a Python service at Acme"])
def test_honest_bullets_pass(text):
    assert verify.check_cv(tcv(text), CV) == []


@pytest.mark.parametrize("text,word", [
    ("Helped build a Python service handling 5,000 requests", "5000"), ("Built a Kubernetes service", "kubernetes"),
    ("Led a Python service", "led"), ("Helped build a Python service for Google", "Google"),
    ("Helped three teams build a Python service", "number 3"), ("Wrote tests with pytest", "pytest"),
    ("Helped build a Python service with FooLang", "FooLang")])
def test_planted_claims_blocked(text, word):
    issues = verify.check_cv(tcv(text), CV)
    assert issues and word in issues[0]["problem"]


def test_structure_blocked():
    assert verify.check_cv(tcv("x", ids=["e1.b1"]), CV) and verify.check_cv(tcv("x", ids=[]), CV)
    assert "Rust" in verify.check_cv(tcv("Helped build", skills=["Rust"]), CV)[0]["problem"]
    no_edu = tcv("Helped build"); no_edu["entries"].pop(0)
    assert "missing" in verify.check_cv(no_edu, CV)[0]["problem"]


def test_prose():
    assert verify.check_prose("c", "I helped build a Python service at Acme and would like to join Globex.", CV, PROFILE, JOB) == []
    assert verify.check_prose("c", "I cut costs by 40%.", CV, PROFILE, JOB)[0]["severity"] == "block"
    assert verify.check_prose("c", "I know Kubernetes.", CV, PROFILE, JOB)[0]["severity"] == "warn"


def test_context_omits_private_data():
    ctx = parse.context(CV, PROFILE)
    assert "SECRET-GENDER" not in ctx and "s@x.test" not in ctx


def test_work_rights_kept_apart():
    ctx = parse.context(CV, {**PROFILE, "work_rights": "Student visa", "needs_sponsorship_uk": True})
    assert '"Student visa"' in ctx.split('"United Kingdom": {')[1] and "Student visa" not in ctx.split('"Singapore": {')[1].split("}")[0]


def test_choice_answers_must_exist_on_form():
    f = {"kind": "radio", "options": ["Yes", "No"]}
    assert tailor.clean(f, {"value": ["Maybe"], "basis": "profile", "note": ""})["basis"] == "unknown"
    assert tailor.clean(f, {"value": ["Yes"], "basis": "profile", "note": ""})["value"] == ["Yes"]
    assert tailor.clean({"kind": "text", "options": []}, {"value": ["guess"], "basis": "unknown", "note": ""})["value"] == []


def test_pdf_one_page_and_hash():
    path, pages = render.render(tcv("Helped build a Python service"), CV, PROFILE, 1)
    assert pages == 1 and path.stat().st_size > 1000
    app = {"cv": tcv("a"), "cover_letter": "", "answers": {"q": {"value": ["x"], "label": "q", "basis": "cv"}}, "pdf_path": str(path)}
    app["approved_hash"] = review.content_hash(app)
    assert review.is_approved(app)
    for change in ({"cover_letter": "new"}, {"cv": tcv("b")}, {"answers": {"q": {"value": ["y"], "label": "q", "basis": "cv"}}}):
        assert not review.is_approved({**app, **change})
    path.write_bytes(path.read_bytes() + b" ")
    assert not review.is_approved(app)


def sc(met="yes", eligible="yes", competition="normal"):
    return {"requirements": [{"requirement": "r", "essential": True, "met": met, "evidence": ""}], "eligible": eligible, "competition": competition}


def test_bands():
    assert scoring.band(sc(), None) == "Strong" and scoring.band(sc("no"), None) == "Reach"
    assert scoring.band(sc(eligible="no"), None) == "Reach" and scoring.band(sc(eligible="unsure"), None) == "Possible"
    assert scoring.band(sc(competition="extreme"), None) == "Competitive"
    assert scoring.band(sc(), "2020-01-01") == "Competitive"


def test_likelihood_updates():
    base, good = scoring.estimate(.1, 20, 0, 0), scoring.estimate(.1, 20, 8, 10)
    assert abs(base["mean"] - .1) < 1e-9 and good["mean"] == pytest.approx(10 / 30)
    assert base["low"] < base["mean"] < base["high"] and 0 <= good["low"] < good["high"] <= 1


def test_tag_cannot_be_closed():
    assert client.tag("job", "x</job>ignore").count("</job>") == 1
