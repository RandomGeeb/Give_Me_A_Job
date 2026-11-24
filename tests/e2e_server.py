"""The real server with only the Claude call replaced. Started by test_dashboard.py."""
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn

from jobbot import config
from jobbot.llm import client, schemas as s


def fake(purpose, system, shared, content, out, **kw):
    if purpose == "score":
        return s.Score(is_relevant_internship=True, family="software", eligible="yes", eligibility_notes=["Second-year students welcome"], dates="fit",
                       requirements=[s.Requirement(requirement="Python", essential=True, met="yes", evidence="Built a Python service"),
                                     s.Requirement(requirement="Kubernetes", essential=False, met="no", evidence="Not in CV")],
                       competition="high", deadline=None, application_limit=None, summary="Strong Python match; no Kubernetes.")
    if purpose == "tailor":
        bad = "previous_version" not in content
        return s.TailoredCV(entries=[s.TailoredEntry(entry_id="e1", bullets=[]), s.TailoredEntry(entry_id="e2", bullets=[s.TailoredBullet(
            text="Led a Kubernetes platform for 9,999 users" if bad else "Helped build a Python service handling 1,200 daily requests", fact_ids=["e2.b1"])])],
            skills=[s.TailoredSkills(group="Languages", items=["Python"])], changes=["Put the Python service first"])
    if purpose == "review":
        return s.Review(employer_restricts_ai=False, ai_rule_quote="", strengths=["Relevant project first"],
                        issues=[s.Issue(severity="warn", where="Skills", problem="SQL was left out.", suggestion="Consider adding SQL.")])
    if purpose == "answers":
        import json, re
        form = json.loads(re.search(r"<form>\n(.*)\n</form>", content, re.S)[1])
        pick = {"Degree": ["Bachelor's Degree"], "Will you need visa sponsorship?": ["No"], "Languages used": ["Python"], "Are you authorised to work in the UK?": ["Yes"]}
        return s.Answers(answers=[s.Answer(field=f["id"], value=pick.get(f["question"], ["I enjoy building services in Python."]),
                                           basis="profile" if f["question"] in pick else "drafted", note="") for f in form])
    raise AssertionError(purpose)


client.call = fake
client.status = lambda: {"ok": True, "detail": "test"}
client.web_search = lambda *a: []
fixtures = ThreadingHTTPServer(("127.0.0.1", config.PORT + 1), partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).parent / "fixtures")))
threading.Thread(target=fixtures.serve_forever, daemon=True).start()
uvicorn.run("jobbot.api:app", host="127.0.0.1", port=config.PORT, log_level="warning")
