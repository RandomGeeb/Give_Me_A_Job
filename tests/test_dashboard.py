import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from test_writing import CV

ROOT = Path(__file__).parent.parent
SHOTS = os.environ.get("SHOTS")
pytestmark = pytest.mark.skipif(not (ROOT / "frontend/dist").exists(), reason="dashboard not built")


@pytest.fixture
def base(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "JOBBOT_HOME": str(tmp_path), "JOBBOT_PORT": str(port), "JOBBOT_HEADLESS": "1"}
    p = subprocess.Popen([sys.executable, str(ROOT / "tests/e2e_server.py")], env=env)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), .2).close()
            break
        except OSError:
            time.sleep(.1)
    yield f"http://127.0.0.1:{port}", port
    p.terminate()
    p.wait(10)


def test_full_path(base):
    url, port = base
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1360, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        shot = lambda n: SHOTS and page.screenshot(path=f"{SHOTS}/{n}.png", full_page=True)
        page.goto(url)
        expect(page.get_by_text("Nothing shortlisted yet")).to_be_visible()
        # CV facts saved through the API, then confirmed on the page.
        page.evaluate("""cv => fetch('/api/cv', {method:'PUT', headers:{'x-jobbot':'1','content-type':'application/json'}, body: JSON.stringify({cv, confirmed:false})})""", CV)
        page.goto(url + "/profile")
        todo = page.get_by_label("Still to do")
        expect(todo.get_by_role("button", name="First name")).to_be_visible()
        todo.get_by_role("button", name="Graduating").click()
        expect(page.locator("#f-graduation input")).to_be_focused()
        page.locator("#f-work_rights input").fill("Student Visa")
        expect(page.locator("#f-work_rights .why")).to_have_text("Add the work limits and end date shown for your visa.")
        page.locator("#f-work_rights input").fill("British citizen")
        expect(page.locator("#f-work_rights .why")).to_have_count(0)
        page.locator("#f-city input").fill("Leeds")
        shot("flags")
        page.locator("#f-first_name input").fill("Sam")
        page.locator("#f-last_name input").fill("Test")
        page.locator("#f-email input").fill("s@x.test")
        # Confirming the CV must not lose edits that have not been saved.
        expect(page.get_by_text("Unsaved changes")).to_be_visible()
        page.get_by_role("button", name="Confirm these facts").click()
        expect(page.get_by_text("Confirmed. Every tailored CV")).to_be_visible()
        expect(page.locator("#f-city input")).to_have_value("Leeds")
        page.get_by_role("button", name="Save profile").click()
        expect(page.get_by_text("Saved", exact=True)).to_be_visible()
        expect(page.get_by_text("Unsaved changes")).to_have_count(0)
        shot("profile")
        page.goto(url + "/discover")
        page.get_by_role("button", name="Add by URL").click()
        page.get_by_label("Web address").fill(f"http://127.0.0.1:{port + 1}/form.html")
        page.get_by_label("Company").fill("Globex")
        page.get_by_label("Role", exact=True).fill("Software Engineer Intern")
        page.get_by_label("Job description").fill("We build services in Python and Kubernetes. " * 10)
        page.get_by_role("button", name="Add role").click()
        page.get_by_role("button", name=re.compile("Score next 1")).click()
        expect(page.get_by_text("Strong Python match")).to_be_visible(timeout=15000)
        shot("discover")
        page.get_by_role("link", name=re.compile("Globex")).click()
        page.get_by_role("button", name="Shortlist").click()
        page.get_by_role("button", name="Tailor application").click()
        expect(page.get_by_role("button", name="Approve this version")).to_be_visible(timeout=30000)
        # The planted false claim was caught in code and revised away.
        expect(page.get_by_text("1 automatic revision")).to_be_visible()
        shot("review")
        page.get_by_role("tab", name="CV", exact=True).click()
        expect(page.get_by_label("Bullet").first).to_have_value("Helped build a Python service handling 1,200 daily requests")
        page.wait_for_timeout(800)
        shot("cv")
        page.get_by_role("button", name="Approve this version").click()
        # This site could not be read in advance, so its answers are written and entered in one go, for checking on the form.
        here = page.evaluate("fetch('/api/jobs/1').then(r => r.json()).then(j => j.apply_url)")
        page.get_by_role("button", name="Use a different link").click()
        page.get_by_label("Link to the application form").fill(here + "?again")
        shot("link")
        page.get_by_role("button", name="Use this link").click()
        expect(page.get_by_label("Link to the application form")).to_have_count(0)
        assert page.evaluate("fetch('/api/jobs/1').then(r => r.json()).then(j => j.apply_url)") == here + "?again"
        page.get_by_role("button", name="Fill application").click()
        expect(page.get_by_text("Answer the red ones")).to_be_visible(timeout=60000)
        expect(page.get_by_text(re.compile(r"Yellow, to check"))).to_be_visible()
        shot("walk")
        fill = page.get_by_role("button", name="Fill this page")
        expect(fill).to_be_enabled(timeout=30000)
        fill.click()
        expect(page.get_by_text(re.compile(r"\d+ fields filled"))).to_be_visible(timeout=60000)
        expect(page.locator(".checklist li", has_text="Why this team?")).to_be_visible()
        shot("filled")
        page.get_by_role("tab", name="Answers").click()
        expect(page.get_by_text("AI draft").first).to_be_visible()
        shot("answers")
        expect(page.get_by_text("Reviewing…")).to_have_count(0, timeout=15000)
        expect(page.locator(".panel li", has_text="privacy policy")).to_be_visible()
        expect(page.locator(".panel li", has_text="Date of birth")).to_be_visible()
        page.get_by_label("Change status").select_option("submitted")
        expect(page.locator(".pill", has_text="Submitted")).to_be_visible()
        page.get_by_label("Change status").select_option("interview")
        expect(page.locator(".pill", has_text="Interview")).to_be_visible()
        page.goto(url)
        expect(page.locator(".col", has_text="Interview").get_by_text("Globex")).to_be_visible()
        shot("pipeline")
        page.goto(url + "/insights")
        expect(page.get_by_text("You have 1.")).to_be_visible()
        shot("insights")
        page.set_viewport_size({"width": 390, "height": 800})
        for path in ("", "/review", "/discover", "/insights", "/profile", "/job/1"):
            page.goto(url + path)
            page.wait_for_timeout(400)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
        assert errors == []
        b.close()


def test_applied_and_limits(base):
    url, port = base
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        shot = lambda n: SHOTS and page.screenshot(path=f"{SHOTS}/{n}.png", full_page=True)
        page.goto(url)
        send = lambda path, body, method="POST": page.evaluate("""([u, b, m]) => fetch('/api' + u, {method: m, headers: {'x-jobbot': '1', 'content-type': 'application/json'},
            body: JSON.stringify(b)}).then(r => r.json())""", [path, body, method])
        job = lambda n, company, text, *to: [(i := send("/jobs", {"url": f"https://example.com/{n}", "company": company, "title": f"Software Intern {n}", "description": text + " We build things. " * 30})["id"]),
                                             *[send(f"/jobs/{i}/move", {"to": s}) for s in to]][0]
        one = "Please only apply for one internship position, choosing the role that best matches your skills."
        a, c = job(1, "Acme", one, "shortlisted"), job(2, "Acme Early Careers", "", "shortlisted")
        job(3, "Beta", "Candidates may submit a maximum of 3 applications within a 6-month period.", "shortlisted", "submitted")
        job(4, "Gamma", "We can only consider one application in 30 days.", "shortlisted", "submitted")
        g = job(5, "Gamma", "", "shortlisted")
        job(6, "Delta", "", "shortlisted", "submitted")
        page.goto(url + "/applied")
        expect(page.get_by_text("3 applied")).to_be_visible()
        row = lambda n: page.locator(".limits tr", has_text=n)
        expect(row("Acme")).to_contain_text("Pick 1 of 2")
        expect(row("Gamma")).to_contain_text("Limit reached")
        expect(row("Gamma")).to_contain_text("again")
        expect(row("Beta")).to_contain_text("3 in any 6 months")
        expect(row("Delta")).to_contain_text("None found")
        shot("applied")
        page.get_by_role("button", name="Add one I made elsewhere").click()
        page.get_by_label("Employer").fill("Acme Ltd")
        page.get_by_label("Role").fill("Data Intern")
        page.get_by_role("button", name="Add", exact=True).click()
        expect(page.get_by_text("4 applied")).to_be_visible()
        expect(row("Acme").first).to_contain_text("Limit reached")
        row("Acme").first.get_by_role("button", name="Change limit").click()
        shot("applied-edit")
        page.get_by_label("Applications allowed").fill("5")
        page.get_by_role("button", name="Save").click()
        expect(row("Acme").first).to_contain_text("Set by you")
        expect(row("Acme").first).not_to_contain_text("Limit reached")
        page.get_by_role("button", name="Remove").click()
        expect(page.get_by_text("3 applied")).to_be_visible()
        page.goto(f"{url}/job/{g}")
        expect(page.get_by_text("You have used this up")).to_be_visible()
        shot("applied-job")
        page.goto(url)
        expect(page.locator(".card", has_text="Gamma").get_by_text("Limit reached")).to_be_visible()
        shot("applied-board")
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(url + "/applied")
        expect(page.get_by_text("3 applied")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        b.close()


def test_review_search_and_filters(base):
    url, port = base
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        rows = [("Acme", "Software Intern", 1, "software", "London, UK", "needs_review", None), ("Beta", "Data Intern", 2, "data_ml_quant", "Singapore", "needs_review", "soon"),
                ("Citadel", "Analyst Intern", 3, "analyst", "Leeds, UK", "approved", None), ("Acme", "Product Intern", 2, "product", "London, UK", "needs_review", "past")]
        job = lambda n, r: {"id": n, "company": r[0], "title": r[1], "tier": r[2], "family": r[3], "locations": [r[4]], "regions": ["sg" if r[4] == "Singapore" else "uk"], "status": r[5],
                            "deadline": {"soon": time.strftime("%Y-%m-%d", time.localtime(time.time() + 3 * 86400)), "past": "2026-01-01"}.get(r[6]), "band": None, "updated_at": f"2026-11-2{n}", "summary": None,
                            "task": None, "limit": {"state": "pick", "n": 1, "days": 365, "used": 0, "left": 1, "going": 2, "quote": "One only.", "by": "advert", "free_on": None, "others": []} if r[0] == "Acme" else None}
        page.route("**/api/jobs", lambda r: r.fulfill(json=[job(n, x) for n, x in enumerate(rows, 1)]))
        page.goto(url + "/review")
        got = lambda: [x.split("\n")[0] for x in page.locator(".list .grow").all_inner_texts()]
        expect(page.get_by_text("4 roles")).to_be_visible()
        assert got() == ["Beta Data Intern", "Acme Software Intern", "Citadel Analyst Intern", "Acme Product Intern"]      # soonest first, closed last
        SHOTS and page.screenshot(path=f"{SHOTS}/review.png")
        page.get_by_label("Search").fill("london soft")
        expect(page.get_by_text("1 of 4 roles")).to_be_visible()
        page.wait_for_url("**q=london**")
        assert got() == ["Acme Software Intern"]
        page.get_by_role("button", name="Clear filters").click()
        page.get_by_label("Rating").select_option("2")
        expect(page.get_by_text("2 of 4 roles")).to_be_visible()
        assert page.get_by_label("Country").locator("option", has_text="Singapore").inner_text() == "Singapore (1)"
        page.get_by_label("Country").select_option("sg")
        assert got() == ["Beta Data Intern"]
        page.locator(".list a").click()
        page.go_back()
        expect(page.get_by_label("Country")).to_have_value("sg")          # still there when you come back
        assert got() == ["Beta Data Intern"]
        page.get_by_label("Search").fill("nothing like this")
        expect(page.get_by_text("No roles match")).to_be_visible()
        page.get_by_role("button", name="Clear filters").click()
        for label, value, want in (("Status", "approved", 1), ("Role type", "product", 1), ("Limits and deadlines", "pick", 2), ("Limits and deadlines", "soon", 1)):
            page.get_by_label(label, exact=True).select_option(value)
            expect(page.locator(".list li")).to_have_count(want)
            page.get_by_role("button", name="Clear filters").click()
        page.get_by_label("Sort").select_option("company")
        assert got()[:2] == ["Acme Software Intern", "Acme Product Intern"]
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            for path in ("review", "insights", "auto", "profile"):
                page.goto(f"{url}/{path}")
                page.wait_for_timeout(400)
                SHOTS and page.screenshot(path=f"{SHOTS}/c-{scheme}-{path}.png")
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(url + "/review")
        expect(page.get_by_text("4 roles")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        SHOTS and page.screenshot(path=f"{SHOTS}/review-phone.png")
        assert not errors or all("is not valid JSON" in e for e in errors), errors
        b.close()
