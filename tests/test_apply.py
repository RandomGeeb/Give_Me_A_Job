import copy
import inspect
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
    ("Resume", "file", [], "file"), ("AI Policy for Application", "select", ["Yes", "No"], "ai_rule"),
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
                   "Languages used": ["Python"], "Are you authorised to work in the UK?": ["Yes"], "Free all summer?": ["YES"]}
        answers = {**known, **fields.merge(ask, {f["key"]: {"value": drafted[f["label"]], "basis": "drafted", "note": ""} for f in ask})}
        # Even a wrongly stored consent answer must not be ticked.
        answers[by["I agree to the privacy policy"]["key"]]["value"] = ["I agree to the privacy policy"]
        res = fill.fill(page, fs, answers, {"cv": cv})
        v = page.evaluate("""() => ({first: first_name.value, email: email.value, deg: deg.value, why: why.value,
          yn: document.querySelector('[class^=_yesno]').dataset.v, py: document.getElementById('q_1-labeled-checkbox-0').checked,
          java: document.getElementById('q_1-labeled-checkbox-1').checked, auth: f.auth.value, gender: f.g.value, dob: dob.value,
          consent: f.consent.checked, file: resume.files[0]?.name, up: up.files[0]?.name, lazy: f.lazy.value, submits: window.submits, hp: f.honeypot.value})""")
        b.close()
    assert v == {"first": "Sam", "email": "s@x.test", "deg": "Bachelor's Degree", "why": "Because.\nReally.", "yn": "No", "py": True, "java": False,
                 "auth": "Yes", "gender": "", "dob": "", "consent": False, "file": "cv.pdf", "up": "cv.pdf", "lazy": "true", "submits": 0, "hp": ""}
    assert res["failed"] == [] and set(res["needs_you"]) >= {"Date of birth", "I agree to the privacy policy"}
    assert posts == []


def test_no_submit_code():
    src = "".join(p.read_text() for p in Path(fill.__file__).parent.glob("*.py"))
    # One Enter, to search inside a Workday list; the page guard blocks it anywhere else.
    assert src.count('press("Enter")') == 1 and 'press("Enter")' in inspect.getsource(fill._prompt)
    assert "requestSubmit" not in src and ".submit()" not in src


def test_place_matching():
    o = ["London, England, United Kingdom", "London, Ontario, Canada", "London Colney, England, United Kingdom"]
    assert fields.match_option("London", o) == o[0]
    assert fields.match_option("Lond", o) is None


def test_full_name_in_one_box():
    fs = fields.add_keys([{"kind": "text", "label": "First & Last Name", "options": [], "required": True}])
    assert fields.plan(fs, PROFILE)[0][fs[0]["key"]]["value"] == ["Sam Test"]


@pytest.mark.parametrize("label", ["Do any sanctions apply?", "Do you have any contractual obligations to another employer?", "Conflicts of interest"])
def test_legal_statements_never_guessed(label):
    f = {"kind": "radio", "label": label, "options": ["Yes", "No"]}
    assert tailor.clean(f, {"value": ["No"], "basis": "guess", "note": ""})["value"] == []


def test_guess_kept_and_option_case_ignored():
    f = {"kind": "radio", "label": "Happy to relocate?", "options": ["YES", "NO"]}
    assert tailor.clean(f, {"value": ["Yes"], "basis": "guess", "note": "n"}) == {"value": ["YES"], "basis": "guess", "note": "n"}


ASIAN = ["White (English, any other)", "Mixed (White and Asian)", "Asian or Asian British (Indian; Chinese)", "Prefer not to say"]


@pytest.mark.parametrize("kind,value,label,opts,exp", [
    ("gender", "Male", "Gender", ["Woman", "Man"], "Man"), ("gender", "Female", "Gender", ["Woman", "Man"], "Woman"),
    ("gender", "Male", "Do you identify as transgender?", ["Yes", "No"], None), ("pronouns", "he/him", "Pronouns", ["She/Her/Hers", "He/Him/His"], "He/Him/His"),
    ("disability", "None", "Do you have a disability?", ["Yes", "No"], "No"), ("disability", "None", "Do you need reasonable adjustments?", ["Yes", "No"], None),
    ("disability", "None", "Do you have a mental health condition?", ["Yes", "No"], None),
    ("disability", "None", "Do you need adjustments for a disability?", ["Yes", "No"], None),
    ("ethnicity", "Chinese", "Ethnicity", ASIAN, ASIAN[2]), ("ethnicity", "Asian - Chinese", "Ethnicity", ASIAN, ASIAN[2]),
    ("ethnicity", "Chinese", "Are you Hispanic/Latino?", ["Yes", "No"], None), ("ethnicity", "Prefer not to say", "Ethnicity", ASIAN[:3] + ["I don't wish to answer"], "I don't wish to answer"),
])
def test_personal_answers_matched_by_meaning(kind, value, label, opts, exp):
    assert fields.match_personal(kind, value, label, opts) == exp


def test_form_rebuilt_after_the_cv_is_attached(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    html = """<form><div class=field><label for=r>Resume</label><input type=file id=r></div><div id=box></div></form><script>
    const draw=()=>{box.innerHTML='<fieldset><legend>Free all summer?</legend><div role=radio aria-checked=false><label><input type=radio name=a value=1>YES</label></div><div role=radio aria-checked=false><label><input type=radio name=a value=0>NO</label></div></fieldset><div class=field><label for=w>Why us?</label><textarea id=w></textarea></div>';
      // The real control never reports its choice; the element around it does.
      document.querySelectorAll('[name=a]').forEach(x=>x.addEventListener('click',e=>{e.preventDefault();x.closest('[role=radio]').setAttribute('aria-checked','true')}))};
    draw();r.onchange=()=>setTimeout(draw,300)</script>"""
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(html)
        fs = fill.extract(page)
        ans = {f["key"]: {"value": ["YES"] if f["opts"] else ["Because."], "basis": "drafted", "note": ""} for f in fs if f["kind"] != "file"}
        res = fill.fill(page, fs, ans, {"cv": cv})
        assert res["filled"] == 3 and res["failed"] == []
        assert page.input_value("#w") == "Because." and page.get_attribute("[role=radio]", "aria-checked") == "true"
        assert fill.fill(page, fs, ans, {"cv": cv})["kept"] == 0
        b.close()


def test_colours_waiting_and_your_changes():
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.route("http://mock.test/", lambda r: r.fulfill(body=FORM, content_type="text/html"))
        page.goto("http://mock.test/")
        fs = fill.extract(page)
        known, ask = fields.plan(fs, PROFILE)
        vals = {"Degree": ["Bachelor's Degree"], "Why this team?": ["First"], "Will you need visa sponsorship?": ["No"],
                "Languages used": ["Python"], "Are you authorised to work in the UK?": ["Yes"], "Free all summer?": ["YES"]}
        ans = {**known, **fields.merge(ask, {f["key"]: {"value": vals[f["label"]], "basis": "guess", "note": "Assumed."} for f in ask})}
        why = next(f["key"] for f in fs if f["label"] == "Why this team?")
        auth = next(f["key"] for f in fs if f["label"].startswith("Are you authorised"))
        r = fill.fill(page, fs, ans, {}, None, {why})
        outline = lambda s: page.eval_on_selector(s, "e => { const x = e.closest('.field-wrapper, .application-question'); return [x.style.outlineStyle, x.style.outlineColor, x.title] }")
        assert page.input_value("#why") == "" and outline("#why")[2] == "Being written now" and "Why this team?" not in r["needs_you"]
        r = fill.fill(page, fs, ans, {}, {why})
        assert r["filled"] == 1 and r["check"] == [{"label": "Why this team?", "note": "Assumed.", "guess": True}]
        assert page.input_value("#why") == "First" and outline("#why") == ["dashed", "rgb(242, 197, 0)", "Best guess: Assumed."]
        assert outline("#email")[:2] == ["solid", "rgb(26, 152, 80)"]
        # A new answer replaces what the app typed, but never what you typed or chose.
        ans[why]["value"] = ["Second"]
        fill.fill(page, fs, ans, {})
        assert page.input_value("#why") == "Second"
        page.fill("#why", "Mine")
        page.check("input[name=auth][value=No]")
        page.fill("#dob", "1 Jan")
        page.check("input[name=consent]")
        assert outline("#dob")[1] == "rgb(26, 152, 80)"          # red turns green once you answer it
        r = fill.fill(page, fs, ans, {})
        assert page.input_value("#why") == "Mine" and page.is_checked("input[name=auth][value=No]") and page.is_checked("input[name=consent]")
        assert r["kept"] == 4 and "Date of birth" not in r["needs_you"]
        fill.fill(page, fs, ans, {})
        assert page.input_value("#why") == "Mine" and page.is_checked("input[name=auth][value=No]")
        b.close()


# Greenhouse: one block of each kind, "Add another", and a listbox per drop-down.
BLOCKS = """<form id=f><div class=employment--container><button type=button class=add-another-button>Add another</button></div>
<div class=education--container><button type=button class=add-another-button>Add another</button></div><button>Submit application</button></form><script>
let sent = 0; f.onsubmit = e => { sent++; e.preventDefault() };
const M = 'January February March April May June July August September October November December'.split(' ');
const text = (id, l) => `<div class=text-input-wrapper><label for=${id}>${l}<span>*</span></label><input id=${id} aria-required=true></div>`;
const pick = (id, l, o) => `<div class=select><label for=${id}>${l}<span>*</span></label><div class=x__control><input id=${id} role=combobox aria-controls=${id}-l></div>
  <div id=${id}-l role=listbox hidden>${o.map(x => `<div role=option>${x}</div>`).join('')}</div></div>`;
const add = (kind) => { const c = document.querySelector('.' + kind + '--container'), n = c.querySelectorAll('.blk').length, d = document.createElement('div');
  d.className = 'blk ' + (kind === 'employment' ? 'employment-form' : 'education--form');
  d.innerHTML = kind === 'employment' ? text('company-name-' + n, 'Company name') + text('title-' + n, 'Title') + pick('start-date-month-' + n, 'Start date month', M) +
    text('start-date-year-' + n, 'Start date year') + pick('end-date-month-' + n, 'End date month', M) + text('end-date-year-' + n, 'End date year') +
    `<fieldset class=checkbox><input type=checkbox id=current-role-${n}_1 name=current-role><label for=current-role-${n}_1>Current role</label></fieldset><button type=button class=rm>Remove</button>` :
    pick('school--' + n, 'School', ['Newcastle University', 'Other']) + pick('degree--' + n, 'Degree', ["Bachelor's Degree"]);
  c.insertBefore(d, c.lastElementChild);
  d.querySelectorAll('[role=combobox]').forEach(i => { const l = document.getElementById(i.id + '-l'); i.onclick = () => l.hidden = false; i.onkeydown = e => { if (e.key === 'Escape') l.hidden = true };
    l.querySelectorAll('[role=option]').forEach(o => o.onclick = () => { i.value = o.innerText; l.hidden = true }) });
  const r = d.querySelector('.rm'); if (r) r.onclick = () => d.remove(); };
document.querySelectorAll('.add-another-button').forEach(b => b.onclick = () => add(b.parentElement.className.split('--')[0])); add('employment'); add('education');</script>"""


def _two_jobs():
    cv = {"entries": [{"id": "a", "kind": "experience", "title": "Data Engineer Intern", "organisation": "Acme", "location": "", "dates": "Sep 2025 – Present"},
                      {"id": "b", "kind": "experience", "title": "Dev", "organisation": "Beta", "location": "", "dates": "May 2022 – Feb 2023"}]}
    return cv, {"entries": [{"entry_id": x, "bullets": [{"text": "Did it", "fact_ids": []}]} for x in "ab"], "skills": []}


def test_greenhouse_work_history():
    cv, tcv = _two_jobs()
    prof = {**PROFILE, "university": "Newcastle University"}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(BLOCKS)
        go = lambda: (fill.add_panels(page, 2), fs := fill.extract(page), plan := fields.plan(fs, prof, cv, tcv), fill.fill(page, fs, plan[0], {}), plan[1])
        *_, res, ask = go()
        val = lambda s: page.input_value("#" + s)
        row = lambda n: [val(f"{x}-{n}") for x in ("company-name", "title", "start-date-month", "start-date-year", "end-date-month", "end-date-year")]
        assert row(0) == ["Acme", "Data Engineer Intern", "September", "2025", "", ""] and page.is_checked("#current-role-0_1")
        assert row(1) == ["Beta", "Dev", "May", "2022", "February", "2023"] and not page.is_checked("#current-role-1_1")
        assert page.locator(".employment-form").count() == 2 and page.locator(".education--form").count() == 1 and val("school--0") == "Newcastle University"
        assert [f["label"] for f in ask] == ["Degree"] and ask[0]["key"] == "degree|choice"          # answers drafted earlier still fit
        assert res["needs_you"] == ["Degree"] and not res["failed"] and page.evaluate("sent") == 0
        # A block you remove stays removed, and one you filled in yourself is left alone.
        page.locator(".rm").nth(1).click()
        go()
        assert page.locator(".employment-form").count() == 1
        page.goto("about:blank")
        page.set_content(BLOCKS)
        page.fill("#company-name-0", "Mine")
        fill.add_panels(page, 2)
        fs = fill.extract(page)
        assert page.locator(".employment-form").count() == 1 and all(f["theirs"] for f in fs if f.get("section") == "Employment 1")
        b.close()


def test_no_job_beyond_the_cv():
    cv, tcv = _two_jobs()
    fs = fields.add_keys([{"kind": "text", "label": "Employment 3: Company name", "name": "Company name", "section": "Employment 3", "required": True, "options": []}])
    known, ask = fields.plan(fs, PROFILE, cv, tcv)
    assert not ask and list(known.values())[0]["value"] == []
