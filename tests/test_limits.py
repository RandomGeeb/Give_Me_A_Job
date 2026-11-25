"""Limits stated in adverts, what counts as an application, and what the limit stops."""
from datetime import date, timedelta

import pytest

from jobbot import auto, db, limits
from test_flow import c, make, stand_ins, wait  # noqa: F401


@pytest.mark.parametrize("text,n,days", [
    ("Candidates may submit a maximum of 3 applications within a 6-month period.", 3, 183),
    ("Candidates may submit up to 5 applications during any rolling 30-day period.", 5, 30),
    ("Please apply for the single location that you're the most interested in. We can only consider one application in 30 days.", 1, 30),
    ("Candidates may apply to a maximum of two positions across Our Company.", 2, 365),
    ("Each applicant has the opportunity to apply to up to 4 separate business / location combinations in any given recruiting year.", 4, 365),
    ("Please only apply for one internship position.", 1, 365), ("You can only apply for one programme.", 1, 365),
    ("We request that you only apply to the one role you are most interested in.", 1, 365),
    ("you may only submit one application to the Summer Internship Program globally", 1, 365),
    ("If you make more than one application, we will review your first application only.", 1, 365),
    ("We accept one application per candidate, per recruitment cycle.", 1, 365), ("We do not accept multiple applications.", 1, 365),
    ("You must not have applied for any other Maven programmes in this cycle", 1, 365),
])
def test_stated_limits(text, n, days):
    got = limits.find(text)
    assert (got["n"], got["days"]) == (n, days) and got["quote"]


@pytest.mark.parametrize("text", [
    "You do not need to submit multiple applications.", "Work on one of our software applications.", "We will close once we receive 500 applications.",
    "Please apply only if you have the required work authorization.", "Apply by 30 January 2027.", "If you are applying for one of these roles, you must work from the city.",
    "Help our team of up to 4 roles ship faster.", "Only complete applications will be accepted.", "Your application is one step closer.", "",
])
def test_not_limits(text):
    assert limits.find(text) is None


def test_one_employer_many_names():
    for a, b in (("DRW - University Jobs", "DRW "), ("Marshall Wace Internship Programmes ", "Marshall Wace"), ("Graphcore Early Careers", "Graphcore"),
                 ("Susquehanna International Group (SIG)", "Susquehanna International Group"), ("Mitsubishi UFG", "MUFG"), ("palantir", "Palantir"), ("Method, a GlobalLogic company", "Method")):
        assert limits.key(a) == limits.key(b)
    assert limits.key("Citadel") != limits.key("Citadel Securities") and limits.key("Company not shown", 1) != limits.key("Company not shown", 2)


def role(c, n, text, company="Globex", ready=True):
    i = make(c, f"https://job-boards.greenhouse.io/globex/jobs/{n}", f"Software Intern {n}")
    with db.connect() as con:
        con.execute("UPDATE jobs SET company=? WHERE id=?", (company, i))
    c.patch(f"/api/jobs/{i}", json={"description": text + " " + "Build things in Python. " * 20})
    if ready:
        c.post(f"/api/jobs/{i}/move", json={"to": "shortlisted"}); c.post(f"/api/jobs/{i}/tailor"); wait(i)
    return i


def lim(c, i):
    return c.get(f"/api/jobs/{i}").json()["limit"]


ONE = "Please only apply for one internship position."


def test_limit_from_one_advert_covers_the_employer(c, stand_ins):      # noqa: F811
    a, b = role(c, 1, ONE, "Globex Early Careers"), role(c, 2, "")
    assert lim(c, a)["state"] == lim(c, b)["state"] == "pick" and lim(c, b)["going"] == 2
    assert c.post(f"/api/jobs/{a}/approve", json={}).status_code == 200
    r = c.post(f"/api/jobs/{b}/approve", json={})
    assert r.status_code == 409 and "already approved" in r.json()["detail"] and lim(c, b)["state"] == "taken"
    assert c.post(f"/api/jobs/{a}/move", json={"to": "submitted"}).status_code == 200
    r = c.post(f"/api/jobs/{b}/approve", json={})
    assert r.status_code == 409 and "you have made 1" in r.json()["detail"]
    assert lim(c, a)["state"] == "sent" and lim(c, b)["state"] == "reached"
    assert next(j for j in c.get("/api/jobs").json() if j["id"] == b)["limit"]["state"] == "reached"
    # You know better than the advert.
    k = c.get("/api/limits").json()["employers"][0]["key"]
    c.put(f"/api/limits/{k}", json={"n": 2})
    assert lim(c, b)["left"] == 1 and lim(c, b)["by"] == "you" and c.post(f"/api/jobs/{b}/approve", json={}).status_code == 200
    c.put(f"/api/limits/{k}", json={"n": 1})          # used up after approval: the form is not opened either
    assert c.post(f"/api/jobs/{b}/open").status_code == 409
    c.put(f"/api/limits/{k}", json={"n": 0})
    assert lim(c, b) is None
    c.put(f"/api/limits/{k}", json={"n": None})
    assert lim(c, b)["n"] == 1 and c.put("/api/limits/nobody", json={"n": 1}).status_code == 404


def test_what_counts_as_applied(c, stand_ins):      # noqa: F811
    a = role(c, 1, "")
    c.post(f"/api/jobs/{a}/approve", json={})
    c.post(f"/api/jobs/{a}/move", json={"to": "withdrawn"})
    assert c.get("/api/limits").json()["applied"] == []          # dropped before it was sent
    b = role(c, 2, "")
    c.post(f"/api/jobs/{b}/approve", json={})
    c.post(f"/api/jobs/{b}/move", json={"to": "submitted"}); c.post(f"/api/jobs/{b}/move", json={"to": "rejected"})
    assert [x["id"] for x in c.get("/api/limits").json()["applied"]] == [b]
    c.post(f"/api/jobs/{b}/move", json={"to": "submitted"}); c.post(f"/api/jobs/{b}/move", json={"to": "withdrawn"})
    assert len(c.get("/api/limits").json()["applied"]) == 1
    with db.connect() as con:
        db.set_status(con, b, "discovered")          # marked by mistake
    assert c.get("/api/limits").json()["applied"] == []


def test_made_elsewhere_and_periods(c, stand_ins):      # noqa: F811
    i = role(c, 1, "We can only consider one application in 30 days.", ready=False)
    ago = lambda n: (date.today() - timedelta(days=n)).isoformat()
    assert c.post("/api/outside", json={"company": "GLOBEX Ltd", "title": "Analyst", "date": ago(40)}).status_code == 200
    assert lim(c, i)["state"] == "ok" and lim(c, i)["used"] == 0
    c.post("/api/outside", json={"company": "Globex", "title": "Analyst 2", "date": ago(10)})
    assert lim(c, i)["state"] == "reached" and lim(c, i)["free_on"] == ago(-20)
    d = c.get("/api/limits").json()
    assert len(d["applied"]) == 2 and d["employers"][0]["left"] == 0
    c.delete(f"/api/outside/{d['applied'][0]['outside']}")
    assert lim(c, i)["used"] == 0 and len(c.get("/api/limits").json()["applied"]) == 1
    assert c.post("/api/outside", json={"company": "X", "title": "Y", "date": ago(-2)}).status_code == 422


def test_auto_tailor_does_not_exceed_a_stated_limit(c, stand_ins):      # noqa: F811
    ids = [role(c, n, ONE, ready=False) for n in range(3)]
    with db.connect() as con:
        con.execute("UPDATE jobs SET tier=1")
        con.execute("UPDATE jobs SET title='Different '||id")
        queue, held, *_ = auto.candidates(con)
    assert len(queue) == 1 and held == 2 and queue[0]["id"] in ids


def test_advert_read_again_when_it_changes(c, stand_ins):      # noqa: F811
    i = role(c, 1, "", ready=False)
    assert lim(c, i) is None
    c.patch(f"/api/jobs/{i}", json={"description": ONE})
    assert lim(c, i)["n"] == 1
