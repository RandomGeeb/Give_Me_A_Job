"""The multi-page walk against a mock Workday site, in a real browser with the model replaced."""
import copy
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import test_flow
from jobbot import config, pipeline, walk
from jobbot.apply import fields, fill
from jobbot.apply import session as sess
from test_flow import c, make, stand_ins, to_review, wait       # noqa: F401

HTML = (Path(__file__).parent / "fixtures/workday.html").read_text()
PICK = {"How Did You Hear About Us?": ["Website"], "Have you previously worked for Globex?": ["No"], "Degree": ["Bachelors"],
        "Will you require sponsorship?": ["Yes"], "Which visa do you hold?": ["Student"], "When can you start?": ["2027-06-07"]}


@pytest.fixture
def site(c, stand_ins, monkeypatch):      # noqa: F811
    s = sess.Session()
    monkeypatch.setattr(config, "HEADLESS", True)
    monkeypatch.setattr(sess, "session", s)
    monkeypatch.setattr(walk, "WAIT", .4)
    walk.walks.clear()

    def draft(job, cv, profile, fs, *a, **k):
        return {f["key"]: {"value": PICK.get(f["label"].split(": ")[-1], ["I like Python."]), "basis": "guess" if f["label"] in PICK else "drafted", "note": "n"} for f in fs}
    stand_ins.answers.side_effect = draft
    c.put("/api/settings", json={"profile": {**config.DEFAULT_SETTINGS["profile"], "first_name": "Sam", "last_name": "Test", "phone": "+44 7700 900123",
                                             "city": "Leeds", "address": "1 High St", "postcode": "LS1 1AA", "university": "Leeds", "discipline": "Computer Science", "study_start": "September 2025",
                                             "graduation": "June 2028"}})
    dated = copy.deepcopy(test_flow.CV)
    for e in dated["entries"]:
        e["dates"] = "Jun 2025 – Aug 2025"
    monkeypatch.setattr(test_flow, "CV", dated)
    i = to_review(c)
    assert c.post(f"/api/jobs/{i}/approve", json={}).status_code == 200
    s.run(lambda ctx: ctx.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(body=HTML, content_type="text/html")))
    js = lambda e: s.run(lambda ctx: s.pages[i].evaluate(e))
    yield i, s, js
    walk.stop(i)
    s.run(lambda ctx: ctx.close())


def enter(js):
    for _ in range(40):
        if js("!!document.querySelector('#in')"):
            return js("document.querySelector('#in').click()")
        js("document.querySelector('[data-automation-id=SignInWithEmailButton]')?.click()")
        time.sleep(.2)


def until(i, states, name=None, t=90):
    end = time.monotonic() + t
    while time.monotonic() < end:
        w = walk.state(i)
        if w["state"] in states and (name is None or w["name"] == name):
            return w
        time.sleep(.1)
    raise AssertionError(walk.state(i))


def test_whole_application(c, site, stand_ins):      # noqa: F811
    i, s, js = site
    assert c.post(f"/api/jobs/{i}/fill-all", json={}).status_code == 200
    assert c.post(f"/api/jobs/{i}/fill-all", json={}).status_code == 409 and c.post(f"/api/jobs/{i}/fill").status_code == 409
    until(i, {"signin"})
    time.sleep(4)               # the sign-in page draws late, with no password box at first, and is never filled
    assert walk.state(i)["state"] == "signin" and not walk.state(i)["pages"] and js("!window.__jbGuard")
    js("document.querySelector('[data-automation-id=SignInWithEmailButton]').click()")
    time.sleep(1)
    assert js("[em.value, document.querySelector('[data-automation-id=beecatcher]').value].join('')") == ""      # nothing typed while you sign in
    enter(js)
    w = until(i, {"waiting", "stopped"}, "Voluntary Disclosures")
    assert "need" in w["message"] and js("[gen.innerText, terms.checked]") == ["Select One", False]
    j = js("J()")
    assert j["presses"] == 3 and j["submits"] == 0
    v = j["saved"]
    assert v["source"] == ["Company Careers Website"] and v["prevn"] and v["country"] == "United Kingdom" and v["cc"] == ["United Kingdom (+44)"]
    assert (v["fn"], v["ln"], v["a1"], v["city"], v["pc"], v["pt"], v["ph"], v["ext"], v["pref"]) == \
        ("Sam", "Test", "1 High St", "Leeds", "LS1 1AA", "Mobile", "7700900123", "", False)
    assert j["adds"]["edu"] == 1 and j["adds"]["work"] == 1
    assert v["work-1--jobTitle"] and v["work-1--companyName"] and v["work-1--desc"].startswith("• Helped build")
    assert v["edu-1--school"] == "Leeds" and v["edu-1--degree"] == "Bachelors"
    assert v["edu-1--fos"] == ["Computer and Information Science"] or v["edu-1--fos"] == ["Computer Science and Mathematics"]
    assert v["edu-1--from-dateSectionYear-input"] == "2025" and v["edu-1--to-dateSectionYear-input"] == "2028"
    assert (v["work-1--startDate-dateSectionMonth-input"], v["work-1--endDate-dateSectionYear-input"]) == ("06", "2025")
    assert v["skills"] == ["Python"] and v["q1"] == "Yes" and v["q1b"] == "Student"      # the question that appeared was answered too
    assert [v[f"q3-dateSection{p}-input"] for p in ("Day", "Month", "Year")] == ["07", "06", "2027"]
    pages = {p["page"]: p for p in w["pages"]}
    assert list(pages) == ["My Information", "My Experience", "Application Questions", "Voluntary Disclosures"]
    assert any(x["label"] == "How Did You Hear About Us?" and x["guess"] for x in pages["My Information"]["check"])
    assert not any(p["failed"] for p in w["pages"]) and "I consent to the terms and conditions" in pages["Voluntary Disclosures"]["needs_you"]
    # Address and phone never reached the model.
    asked = " ".join(f["label"] for call in stand_ins.answers.call_args_list for f in call.args[3])
    assert "Address" not in asked and "Postal" not in asked and "Phone" not in asked and "Gender" not in asked
    # Going back leaves the pages alone.
    js("document.querySelector('[data-automation-id=pageFooterBackButton]').click()")
    w = until(i, {"waiting"}, "Application Questions")
    time.sleep(2)
    assert "went back" in walk.state(i)["message"] and js("J().presses") == 3
    js("document.querySelector('[data-automation-id=pageFooterNextButton]').click()")
    until(i, {"waiting"}, "Voluntary Disclosures")
    time.sleep(2)
    js("terms.click(); document.querySelector('[data-automation-id=pageFooterNextButton]').click()")
    w = until(i, {"review"}, "Review")
    assert not w["running"] or not until(i, {"review"})["stop"]
    assert js("J().submits") == 0 and c.get(f"/api/jobs/{i}").json()["status"] == "filling"
    assert wait(i)["kind"] == "review"          # one AI review for all the pages
    assert stand_ins.review.call_count == 2


def test_site_refuses_a_page(c, site):      # noqa: F811
    i, s, js = site
    s.run(lambda ctx: ctx.unroute_all())
    s.run(lambda ctx: ctx.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(body=HTML.replace("location.search.includes('reject')", "true"), content_type="text/html")))
    c.post(f"/api/jobs/{i}/fill-all", json={})
    until(i, {"signin"})
    enter(js)
    w = until(i, {"waiting"}, "My Information")
    assert "Phone Number is not valid" in w["message"] and w["pages"][-1]["errors"] and js("J().presses") == 1
    c.post(f"/api/jobs/{i}/fill-stop", json={})
    until(i, {"stopped"})


def test_window_closed(c, site):      # noqa: F811
    i, s, js = site
    c.post(f"/api/jobs/{i}/fill-all", json={})
    until(i, {"signin"})
    s.run(lambda ctx: s.pages[i].close())
    assert "closed" in until(i, {"stopped"})["message"] and not walk.state(i)["running"]


def test_your_own_link(c, site):      # noqa: F811
    i, s, js = site
    wd = "https://globex.wd3.myworkdayjobs.com/en-US/Jobs/job/London/Intern_R1/apply"
    s.run(lambda ctx: ctx.unroute_all())
    s.run(lambda ctx: ctx.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(body="<h1>An advert with no form</h1>", content_type="text/html")))
    s.run(lambda ctx: ctx.route("https://globex.wd3.myworkdayjobs.com/**", lambda r: r.fulfill(body=HTML, content_type="text/html")))
    c.post(f"/api/jobs/{i}/fill-all", json={})
    assert "No application form" in until(i, {"waiting"})["message"]
    r = c.put(f"/api/jobs/{i}/link", json={"url": f" {wd}/autofillWithResume?source=x "})
    assert r.status_code == 200 and r.json() == {"apply_url": f"{wd}/applyManually?source=x", "same": None}
    until(i, {"signin"})                                       # the open window went there and carried on
    j = c.get(f"/api/jobs/{i}").json()
    assert j["apply_url"].startswith(wd) and j["ats"] == "myworkday" and j["status"] == "filling" and j["application"]["approved"]


def test_link_rules(c, stand_ins):      # noqa: F811
    i = make(c)
    put = lambda u: c.put(f"/api/jobs/{i}/link", json={"url": u})
    orig = c.get(f"/api/jobs/{i}").json()["apply_url"]
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "not a link", "https://www.linkedin.com/jobs/1", "https://a:b@x.test/1", "https://x.test/a b"):
        assert put(bad).status_code == 409, bad
    assert c.get(f"/api/jobs/{i}").json()["apply_url"] == orig
    assert put("acme.wd1.myworkdayjobs.com/x/job/1").json()["apply_url"] == "https://acme.wd1.myworkdayjobs.com/x/job/1"
    two = c.post("/api/jobs", json={"url": "https://x.test/9", "company": "Acme", "title": "Data Intern"}).json()["id"]
    assert c.put(f"/api/jobs/{two}/link", json={"url": "https://acme.wd1.myworkdayjobs.com/x/job/1"}).json()["same"]["id"] == i
    # Finding the advert again does not undo it; a blank does.
    c.post("/api/jobs", json={"url": "https://job-boards.greenhouse.io/globex/jobs/1", "company": "Globex", "title": "Software Engineer Intern"})
    assert "myworkdayjobs" in c.get(f"/api/jobs/{i}").json()["apply_url"]
    assert put("").json()["apply_url"] == orig


def test_needs_approval(c, stand_ins):      # noqa: F811
    i = to_review(c)
    assert c.post(f"/api/jobs/{i}/fill-all", json={}).status_code == 409


@pytest.mark.parametrize("body", ["<h1>Sign In</h1><form><label for=e>Email Address</label><input id=e><button>Next</button></form>",
                                  "<h1>Check your email</h1><label for=e>Code</label><input id=e><button>Continue</button>"])
def test_pages_on_the_way_to_the_form_are_left_alone(c, site, body):      # noqa: F811
    i, s, js = site
    s.run(lambda ctx: ctx.unroute_all())
    s.run(lambda ctx: ctx.route("**/*", lambda r: r.fulfill(body=body, content_type="text/html")))
    for url, want in (("https://globex.wd3.myworkdayjobs.com/en-US/Jobs/login", "signin"), ("https://globex.wd3.myworkdayjobs.com/en-US/Jobs/other", "waiting"),
                      ("https://careers.globex.test/verify", "waiting")):
        assert c.put(f"/api/jobs/{i}/link", json={"url": url}).status_code == 200
        c.post(f"/api/jobs/{i}/fill-all", json={})
        until(i, {want})
        time.sleep(1.5)
        assert walk.state(i)["state"] == want and not walk.state(i)["pages"] and js("e.value") == "" and js("!window.__jbGuard")
        walk.stop(i)
        until(i, {"stopped"})


@pytest.mark.parametrize("html,safe", [
    ("<p>Step 1 of 3</p><button>Next</button>", True),
    ("<p>Step 3 of 3</p><button>Next</button>", False),                              # last step
    ("<p>Step 1 of 3</p><button>Next</button><button>Submit application</button>", False),
    ("<p>Step 1 of 3</p><button>Submit</button>", False),
    ("<p>Step 1 of 3</p><button>Continue to submit</button>", False),
    ("<p>Step 1 of 3</p><button>Next</button><button>Continue</button>", False),      # which one?
    ("<button>Continue</button>", False),                                             # no telling whether more follows
    ("<p>Page 1 of 9</p><button>Next</button>", False),                               # search results
    ("<ol><li><a aria-current=step>You</a></li><li>Review</li></ol><button>Save and continue</button>", True),
    ("<ol><li>You</li><li><a aria-current=step>Review</a></li></ol><button>Continue</button>", False),
    ("<ol data-automation-id=progressBar><li data-automation-id=progressBarCompletedStep>a</li><li data-automation-id=progressBarActiveStep>Review</li>"
     "</ol><button data-automation-id=pageFooterNextButton>Submit</button>", False),
    ("<ol data-automation-id=progressBar><li data-automation-id=progressBarActiveStep>Review</li><li data-automation-id=progressBarInactiveStep>x</li>"
     "</ol><button data-automation-id=pageFooterNextButton>Continue</button>", False),
])
def test_only_presses_next_when_more_pages_follow(html, safe):
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content(html + "<script>n=0;document.querySelectorAll('button').forEach(b=>b.onclick=()=>n++)</script>")
        assert fill.step(page)["safe"] is safe and fill.press_next(page) is safe and page.evaluate("n") == int(safe)
        page.set_content("<p>Step 1 of 3</p><button>Next</button><script>n=0;document.querySelector('button').onclick=()=>n++</script>")
        fill.step(page)
        page.evaluate("document.querySelector('button').innerText='Submit'")           # changed between looking and pressing
        assert not page.evaluate(fill.PRESS) and page.evaluate("n") == 0
        b.close()


def test_dates():
    assert fields.dates("Sep 2025 – Present") == ("2025-09", "", True)
    assert fields.dates("May 2022 - Feb 2023") == ("2022-05", "2023-02", False)
    assert fields.dates("2021") == ("2021", "", False) and fields.dates("") == ("", "", False)


def test_current_job_has_no_end_date():
    cv = {"entries": [{"id": "a", "kind": "experience", "title": "Dev", "organisation": "Acme", "location": "Leeds", "dates": "Sep 2025 – Present"}]}
    tcv = {"entries": [{"entry_id": "a", "bullets": [{"text": "Did it", "fact_ids": []}]}], "skills": []}
    fs = fields.add_keys([{"kind": k, "label": f"Work Experience 1: {n}", "name": n, "section": "Work Experience 1", "required": True, "options": o} for k, n, o in
                          (("checkbox", "I currently work here", [{"label": "I currently work here"}]), ("dateparts", "From", []), ("dateparts", "To", []), ("text", "Company", []))])
    known, ask = fields.plan(fs, config.DEFAULT_SETTINGS["profile"], cv, tcv)
    assert [known[f["key"]]["value"] for f in fs] == [["I currently work here"], ["2025-09"], [], ["Acme"]] and not ask
    assert not fs[2]["required"] and fs[0]["policy"] == "ok"


def test_graduation_date():
    ask = lambda g, parts, label="Please select your anticipated graduation date.": fields.plan(fields.add_keys(
        [{"kind": "dateparts", "label": label, "parts": parts, "required": True, "options": []}]), {**config.DEFAULT_SETTINGS["profile"], "graduation": g})
    val = lambda *a: list(ask(*a)[0].values())[0]["value"]
    assert val("30 June 2028", ["month", "day", "year"]) == ["2028-06-30"] and val("30th Jun 2028", ["day", "month", "year"]) == ["2028-06-30"]
    assert val("30 June 2028", ["month", "year"]) == ["2028-06"] and val("June 2028", ["month", "day", "year"]) == []
    assert ask("30 June 2028", ["day", "month", "year"], "When can you start?")[1]          # other dates are not affected
    assert fields.month("30 June 2028") == "2028-06"
