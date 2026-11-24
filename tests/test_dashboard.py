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
        page.get_by_role("button", name="Open form").click()
        fill = page.get_by_role("button", name="Fill this page")
        expect(fill).to_be_enabled(timeout=30000)
        fill.click()
        # This site could not be read in advance, so its drafted answers need approval first.
        expect(page.get_by_text("new questions drafted")).to_be_visible(timeout=60000)
        page.get_by_role("tab", name="Answers").click()
        expect(page.get_by_text("AI draft").first).to_be_visible()
        shot("answers")
        expect(page.get_by_text("Reviewing…")).to_have_count(0, timeout=15000)
        page.get_by_role("button", name="Approve this version").click()
        page.get_by_role("button", name="Open form").click()
        expect(fill).to_be_enabled(timeout=30000)
        fill.click()
        expect(page.get_by_text(re.compile(r"\d+ fields filled"))).to_be_visible(timeout=60000)
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
