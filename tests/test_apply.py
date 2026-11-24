import copy
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jobbot.apply import fields, fill, policy
from jobbot.config import DEFAULT_SETTINGS
from jobbot.cv import tailor

FORM = (Path(__file__).parent / "fixtures/form.html").read_text()
PROFILE = {**copy.deepcopy(DEFAULT_SETTINGS["profile"]), "first_name": "Sam", "last_name": "Test", "email": "s@x.test"}


@pytest.mark.parametrize("label,kind,opts,exp", [
    ("What is your gender?", "radio", [], "sensitive:gender"), ("Gender identity", "select", [], "sensitive:gender"),
    ("Race/Ethnicity", "combobox", [], "sensitive:ethnicity"), ("Do you have a disability?", "radio", [], "sensitive:disability"),
    ("Do you need reasonable adjustments?", "textarea", [], "sensitive:disability"), ("Date of birth", "text", [], "sensitive:age"),
    ("Any unspent convictions?", "radio", [], "sensitive:criminal"), ("National Insurance number", "text", [], "sensitive:identity"),
    ("Passport number", "text", [], "sensitive:identity"), ("Were you eligible for free school meals?", "radio", [], "sensitive:socioeconomic"),
    ("Something else", "radio", ["A", "Prefer not to say"], "sensitive:other"),
    ("I agree to the privacy policy", "checkbox", ["x"], "consent"), ("Consent to AI notetakers?", "radio", ["Yes, I consent", "No"], "consent"),
    ("Keep me updated", "checkbox", ["Yes"], "consent"), ("Answer without using AI: why us?", "textarea", [], "ai_rule"),
    ("Resume", "file", [], "file"),
    ("Name Pronunciation", "text", [], "ok"), ("Describe a trace you debugged", "textarea", [], "ok"),
    ("What language do you use?", "text", [], "ok"), ("Are you authorised to work in the UK?", "radio", ["Yes", "No"], "ok"),
    ("Tell us about your machine learning experience", "textarea", [], "ok"),
])
def test_policy(label, kind, opts, exp):
    assert policy.classify(label, kind, opts) == exp


def test_sensitive_never_reaches_model_and_consent_never_set():
    fs = fields.add_keys([{"kind": "radio", "label": "Gender", "options": [{"label": "Woman"}], "required": True},
                          {"kind": "checkbox", "label": "I agree", "options": [{"label": "I agree"}], "required": True},
                          {"kind": "textarea", "label": "Why us?", "options": [], "required": True}])
    known, ask = fields.plan(fs, {**PROFILE, "sensitive": {"gender": "Woman"}})
    assert [f["label"] for f in ask] == ["Why us?"]
    assert known["gender|choice"]["value"] == ["Woman"] and known["gender|choice"]["basis"] == "contact"
    assert known["i agree|checkbox"]["value"] == []


def test_long_option_lists():
    f = {"kind": "select", "options": [f"Uni {i}" for i in range(200)] + ["Imperial College London"]}
    assert tailor.clean(f, {"value": ["Imperial College London"], "basis": "cv", "note": ""})["value"]
    assert not tailor.clean(f, {"value": ["Nowhere"], "basis": "cv", "note": ""})["value"]


def test_fill_mock_form(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    posts = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
        page.route("http://mock.test/", lambda r: r.fulfill(body=FORM, content_type="text/html"))
        page.goto("http://mock.test/")
        fs = fill.extract(page)
        by = {f["label"]: f for f in fs}
        assert by["Degree"]["opts"] == ["Bachelor's Degree", "Master's Degree"]
        assert by["Languages used"]["opts"] == ["Python", "Java"] and by["Will you need visa sponsorship?"]["kind"] == "yesno"
        assert not any("honeypot" in str(f) or "captcha" in str(f) for f in fs)
        assert by["First Name"]["required"] and by["Are you authorised to work in the UK?"]["required"]
        known, ask = fields.plan(fs, PROFILE)
        drafted = {"Degree": ["Bachelor's Degree"], "Why this team?": ["Because.\nReally."], "Will you need visa sponsorship?": ["No"],
                   "Languages used": ["Python"], "Are you authorised to work in the UK?": ["Yes"]}
        answers = {**known, **fields.merge(ask, {f["key"]: {"value": drafted[f["label"]], "basis": "drafted", "note": ""} for f in ask})}
        # Even a wrongly stored consent answer must not be ticked.
        answers[by["I agree to the privacy policy"]["key"]]["value"] = ["I agree to the privacy policy"]
        res = fill.fill(page, fs, answers, {"cv": cv})
        v = page.evaluate("""() => ({first: first_name.value, email: email.value, deg: deg.value, why: why.value,
          yn: document.querySelector('[class^=_yesno]').dataset.v, py: document.getElementById('q_1-labeled-checkbox-0').checked,
          java: document.getElementById('q_1-labeled-checkbox-1').checked, auth: f.auth.value, gender: f.g.value, dob: dob.value,
          consent: f.consent.checked, file: resume.files[0]?.name, submits: window.submits, hp: f.honeypot.value})""")
        b.close()
    assert v == {"first": "Sam", "email": "s@x.test", "deg": "Bachelor's Degree", "why": "Because.\nReally.", "yn": "No", "py": True, "java": False,
                 "auth": "Yes", "gender": "", "dob": "", "consent": False, "file": "cv.pdf", "submits": 0, "hp": ""}
    assert res["failed"] == [] and set(res["needs_you"]) >= {"Date of birth", "I agree to the privacy policy"}
    assert posts == []


def test_no_submit_code():
    src = "".join(p.read_text() for p in Path(fill.__file__).parent.glob("*.py"))
    assert 'press("Enter")' not in src and "requestSubmit" not in src and ".submit()" not in src


def test_place_matching():
    o = ["London, England, United Kingdom", "London, Ontario, Canada", "London Colney, England, United Kingdom"]
    assert fields.match_option("London", o) == o[0]
    assert fields.match_option("Lond", o) is None
