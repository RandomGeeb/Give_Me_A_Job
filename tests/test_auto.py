import threading
import time
from datetime import date, timedelta

import pytest

from jobbot import auto, costs, db, pipeline, scoring
from jobbot.llm import client
from jobbot.llm.client import LLMError
from jobbot.llm.schemas import Rating, Ratings
from jobbot.models import RawJob, Status
from jobbot.sources import discover
from test_flow import SCORE, c, letter, stand_ins, to_review, wait  # noqa: F401
from test_writing import CV

LONG = "We use Python and need you to have studied it. " * 10


def rating(id, **k):
    return Rating(**{"id": id, "is_relevant_internship": True, "eligible": "yes", "dates": "fit", "must_met": 9, "must_partial": 0, "must_missing": 0, "must_unknown": 0,
                     "nice_met": 0, "nice_missing": 0, "competition": "normal", "deadline": None, "reason": "ok", **k})


def add(title="Software Intern", company="Globex", desc=LONG, deadline=None, tier=None, n=[0]):
    n[0] += 1
    with db.connect() as con:
        i, _ = db.upsert_job(con, RawJob(key=f"k{n[0]}", source="t", company=company, title=f"{title} {n[0]}", locations=["London, UK"], url=f"https://x.test/{n[0]}",
                                         description=desc, deadline=deadline), Status.discovered, None, "software")
        if tier:
            con.execute("UPDATE jobs SET tier=? WHERE id=?", (tier, i))
    return i


def get(i):
    with db.connect() as con:
        return db.get_job(con, i)


@pytest.fixture
def ready(monkeypatch):
    pipeline.tasks.clear()
    auto.state.update(auto.blank(), _fails=0)
    auto._stop.clear()
    with db.connect() as con:
        db.put_setting(con, "cv", CV)
        db.put_setting(con, "cv_confirmed", True)
    monkeypatch.setattr(client, "status", lambda: {"ok": True, "detail": "t"})
    monkeypatch.setattr(auto, "HOLD", .05)
    monkeypatch.setattr(pipeline, "ensure_description", lambda j: (_ for _ in ()).throw(pipeline.NotReady("no text")) if len(j["description"]) < 200 else j)


def replies(monkeypatch, *rs, log=None):
    """Rates each role in turn by its position in the batch."""
    def call(purpose, system, shared, body, out, **k):
        if log is not None:
            log.append(body)
        return Ratings(ratings=[r for r in rs])
    monkeypatch.setattr(client, "call", call)


def finish():
    for _ in range(400):
        if not auto.state["running"]:
            return
        time.sleep(.02)
    raise AssertionError("did not finish")


@pytest.mark.parametrize("change,want", [({}, 1), ({"must_met": 7, "must_missing": 3}, 1), ({"must_met": 5, "must_missing": 5}, 2), ({"must_met": 1, "must_missing": 8}, 3),
    ({"eligible": "unsure"}, 2), ({"dates": "slight"}, 2), ({"dates": "conflict"}, 4), ({"eligible": "no"}, 4), ({"is_relevant_internship": False}, 4), ({"competition": "extreme"}, 1)])
def test_tiers(change, want):
    s = auto.as_score(rating("j1", **change))
    assert scoring.tier(s, scoring.band(s, None)) == want


def test_rating_run(ready, monkeypatch):
    late = (date.today() - timedelta(days=1)).isoformat()
    a, b, bare, gone = add(), add(title="Research Intern"), add(desc=""), add(deadline=late)
    phd, both = add(title="PhD Software Intern"), add(title="Software Intern, BS/MS/PhD")
    replies(monkeypatch, rating("j1"), rating("j2", eligible="no", reason="Needs a PhD."), rating("j3", must_met=5, must_missing=5), rating("j9"), rating("j1", eligible="no"))
    auto.start("rate"); finish()
    assert (get(a)["tier"], get(a)["status"]) == (1, "discovered")
    assert (get(b)["tier"], get(b)["status"], get(b)["filter_reason"]) == (4, "filtered_out", "Needs a PhD.")
    assert (get(bare)["tier"], get(bare)["triage"]["basis"]) == (3, "title")       # never better than 3 without the advert
    assert get(phd)["triage"]["basis"] == "code" and get(phd)["tier"] == 4 and get(both)["tier"] == 2
    assert get(gone)["tier"] == 4 and auto.state["message"] == "All roles are rated."
    assert auto.summary()["unrated"] == 0 and auto.summary()["bare"] == 1
    discover.reclassify()       # changing settings must not bring back what was ruled out
    assert get(b)["status"] == "filtered_out"


def test_missing_replies_retried_then_left(ready, monkeypatch):
    add(), add()
    log = []
    replies(monkeypatch, rating("j1"), log=log)
    auto.start("rate"); finish()
    assert len(log) == 2 and auto.summary()["unrated"] == 0


def test_old_full_score_only_lowered(ready, monkeypatch):
    i = add()
    with db.connect() as con:
        db.save_score(con, i, {**SCORE, "coverage": .5}, "Possible", "software", None)
        db.set_status(con, i, "scored")
    replies(monkeypatch, rating("j1"))
    auto.start("rate"); finish()
    assert get(i)["tier"] == 2


@pytest.fixture
def work(ready, monkeypatch):
    done = []

    def score(i):
        with db.connect() as con:
            db.save_score(con, i, {**SCORE, "coverage": 1}, "Strong", "software", None)
            db.set_status(con, i, "scored")

    def tailor(i):
        done.append(i)
        with db.connect() as con:
            db.set_status(con, i, "tailoring")
            db.set_status(con, i, "needs_review")
    monkeypatch.setattr(pipeline, "score", score)
    monkeypatch.setattr(pipeline, "tailor_job", tailor)
    return done


def test_order_and_company_cap(work):
    soon = (date.today() + timedelta(days=3)).isoformat()
    t3, t2, t1b, t1a, t4 = add(company="A", tier=3), add(company="B", tier=2), add(company="C", tier=1), add(company="D", tier=1, deadline=soon), add(company="E", tier=4)
    extra = [add(company="C", tier=2), add(company="C", tier=3)]
    bare = add(company="F", tier=1, desc="")
    auto.start("tailor"); finish()
    assert work == [t1a, t1b, t2, extra[0], t3]
    assert get(t4)["status"] == get(bare)["status"] == get(extra[1])["status"] == "discovered"
    s = auto.summary()
    assert s["held"] == 1 and s["bare"] == 1 and [t["tailored"] for t in s["tiers"]] == [2, 2, 1, 0] and auto.state["done"] == 5
    assert get(t3)["status"] == "needs_review"      # and no further: approving is the user's


def test_full_score_can_demote(work, monkeypatch):
    i, j = add(company="A", tier=1), add(company="B", tier=2)

    def score(k):
        with db.connect() as con:
            db.save_score(con, k, {**SCORE, "coverage": .1}, "Reach", "software", None)
            con.execute("UPDATE jobs SET tier=? WHERE id=?", (4 if k == i else 2, k))
            db.set_status(con, k, "filtered_out" if k == i else "scored")
    monkeypatch.setattr(pipeline, "score", score)
    auto.start("tailor"); finish()
    assert work == [j] and "moved to tier 4" in auto.state["finished"][-1]["result"]


def test_waits_for_limits_then_continues(work):
    add(tier=1)
    with db.connect() as con:
        db.put_setting(con, "plan_usage", {"five_hour": {"utilization": .76, "resetsAt": time.time() + 900}})
    auto.start("tailor")
    for _ in range(100):
        if auto.state["waiting"]:
            break
        time.sleep(.02)
    assert "76% used" in auto.state["waiting"] and work == []
    with pytest.raises(pipeline.NotReady, match="already running"):
        auto.start("tailor")
    with db.connect() as con:
        db.put_setting(con, "plan_usage", {"five_hour": {"utilization": .76, "resetsAt": 1}})
    finish()
    assert len(work) == 1


def test_closed_role_found_by_full_score_is_not_tailored(work, monkeypatch):
    i, real = add(tier=1), pipeline.score

    def late(k):
        real(k)
        with db.connect() as con:
            con.execute("UPDATE jobs SET deadline='2020-01-01' WHERE id=?", (k,))
    monkeypatch.setattr(pipeline, "score", late)
    auto.start("tailor"); finish()
    assert work == [] and get(i)["status"] == "scored" and "closed on 2020-01-01" in auto.state["finished"][0]["result"]


def test_stop_while_waiting(work):
    add(tier=1)
    with db.connect() as con:
        db.put_setting(con, "daily_calls", 5)
    auto.start("tailor"); time.sleep(.2); auto.stop(); finish()
    assert work == [] and auto.state["message"] == "Stopped."
    with db.connect() as con:
        assert db.get_setting(con, "auto_mode") is None


def test_stops_after_three_failures(work, monkeypatch):
    for n in range(5):
        add(company=f"C{n}", tier=1)
    tried = []

    def boom(i):
        tried.append(i)
        raise LLMError("not signed in")
    monkeypatch.setattr(pipeline, "tailor_job", boom)
    auto.start("tailor"); finish()
    assert len(tried) == len(set(tried)) == 3 and "repeated problems" in auto.state["message"]


def test_plan_limit_midway_retries_same_role(work, monkeypatch):
    i, real, hit = add(tier=1), pipeline.tailor_job, []

    def first(k):
        if not hit:
            hit.append(1)
            raise LLMError("Your Claude plan has reached its usage limit. Try again after it resets.")
        real(k)
    monkeypatch.setattr(pipeline, "tailor_job", first)
    monkeypatch.setattr(auto._stop, "wait", lambda s: None)
    auto.start("tailor"); finish()
    assert work == [i]


def test_resumes_after_restart(work):
    i = add(tier=1)
    with db.connect() as con:
        db.put_setting(con, "auto_mode", "tailor")
    auto.resume(); finish()
    assert work == [i]


def test_own_rating_stands(ready, c, monkeypatch):      # noqa: F811
    i = add(tier=4)
    with db.connect() as con:
        db.set_status(con, i, "filtered_out")
    assert c.put(f"/api/jobs/{i}/tier", json={"tier": 2}).status_code == 200 and c.put(f"/api/jobs/{i}/tier", json={"tier": 9}).status_code == 422
    monkeypatch.setattr(scoring, "score_job", lambda *a: {**SCORE, "eligible": "no", "eligibility_notes": ["x"]})
    pipeline.run(i, "score", pipeline.score)
    assert (get(i)["tier"], get(i)["status"]) == (2, "scored")


def test_dates_conflict_is_out_of_scope(ready, monkeypatch):
    i = add()
    monkeypatch.setattr(scoring, "score_job", lambda *a: {**SCORE, "dates": "conflict", "eligibility_notes": ["Runs January to June."]})
    pipeline.run(i, "score", pipeline.score)
    assert (get(i)["tier"], get(i)["status"]) == (4, "filtered_out") and "January" in get(i)["filter_reason"]


def test_api_reports_progress(work, c):      # noqa: F811
    add(tier=1)
    hold = threading.Event()
    real = pipeline.tailor_job

    def slow(i):
        pipeline.step(i, "Tailoring your CV")
        hold.wait(5)
        real(i)
    pipeline.tailor_job = slow
    try:
        assert c.post("/api/auto", json={"mode": "tailor"}).status_code == 200
        for _ in range(100):
            a = c.get("/api/auto").json()
            if a["job"] and a["job"]["step"] == "Tailoring your CV":
                break
            time.sleep(.02)
        assert a["job"]["tier"] == 1 and (a["done"], a["total"]) == (0, 1) and c.post("/api/auto", json={"mode": "tailor"}).status_code == 409
        hold.set(); finish()
    finally:
        pipeline.tailor_job = real
    assert c.get("/api/auto").json()["finished"][0]["result"] == "ready to review"


def test_long_advert_keeps_requirements():
    text = "Intro. " * 300 + "\n" + "We like coffee.\n" * 300 + "You must be in your final year of a PhD."
    s = auto.shorten(text)
    assert len(s) <= auto.TEXT and "final year of a PhD" in s and "coffee" not in s


def test_room():
    with db.connect() as con:
        db.put_setting(con, "daily_calls", 10)
        costs.check(con, 10)
        with pytest.raises(costs.LimitReached):
            costs.check(con, 11)
