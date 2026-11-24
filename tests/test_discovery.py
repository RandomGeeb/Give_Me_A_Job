import pytest

from jobbot import db, filters
from jobbot.models import RawJob, Status, can_move
from jobbot.sources import ats
from jobbot.sources.discover import store

ALL = ["software", "data_ml_quant", "project", "product", "analyst"]


def job(title="Software Engineer Intern", locs=("London, UK",), key="k1", company="Acme"):
    return RawJob(key=key, source="t", company=company, title=title, locations=list(locs), url="https://x.test/1")


@pytest.mark.parametrize("loc,ok", [
    ("London, UK", True), ("London", True), ("London, England", True), ("Cambridge, UK", True),
    ("Belfast, Northern Ireland", True), ("Remote in UK", True), ("London, United Kingdom; New York, NY", True),
    ("London, ON, Canada", False), ("London, Ontario", False), ("Cambridge, MA", False), ("Cambridge, Massachusetts", False),
    ("Manchester, NH", False), ("Birmingham, AL", False), ("Oxford, OH", False), ("New York, NY", False),
    ("New York", False), ("Dublin, Ireland", False), ("Boston, New England", False), ("Sydney, New South Wales", False),
    ("Reading, PA", False), ("Singapore", False), ("Hong Kong; London", True), ("Cambridge, MA; London, UK", True),
])
def test_uk(loc, ok):
    assert filters.is_uk([loc]) is ok


@pytest.mark.parametrize("title,fam", [
    ("Software Engineer, Intern", "software"), ("Campus C++ Software Engineer (Intern)", "software"),
    ("Project Management Internship", "project"), ("Programme Management Placement", "project"), ("PMO Intern", "project"),
    ("Product Manager Intern", "product"), ("Technology Business Analyst Intern", "analyst"),
    ("Quantitative Developer Intern", "data_ml_quant"), ("Machine Learning Intern", "data_ml_quant"),
    ("Data Science Placement Year", "data_ml_quant"), ("2027 Technology Summer Analyst", "analyst"),
    ("Cloud Platform Summer Intern 2027", "software"),
])
def test_kept(title, fam):
    assert filters.classify(job(title), ALL) == (fam, None)


@pytest.mark.parametrize("title", [
    "Marketing Intern", "HR Intern", "Hardware Summer Intern 2027", "Senior Software Engineer", "Legal Intern",
    "Internal Tools Software Engineer", "Intern - Biotechnologist (Gene)", "Apprentice Electrician Tech College Intern", "International Product Manager", "Placements Manager", "Intern Recruiter",
])
def test_dropped(title):
    assert filters.classify(job(title), ALL)[1]


def test_family_switch():
    assert "switched off" in filters.classify(job("Product Manager Intern"), ["software"])[1]


@pytest.mark.parametrize("url,exp", [
    ("https://job-boards.greenhouse.io/drweng/jobs/7942281", ("greenhouse", "drweng", "7942281")),
    ("https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=99", ("greenhouse", "stripe", "99")),
    ("https://stripe.com/jobs/search?gh_jid=8130867", ("greenhouse", None, "8130867")),
    ("https://jobs.lever.co/palantir/abc-1/apply", ("lever", "palantir", "abc-1")),
    ("https://jobs.eu.lever.co/foo/abc", ("lever", "eu:foo", "abc")),
    ("https://jobs.ashbyhq.com/airwallex/x-1/application?embed=true", ("ashby", "airwallex", "x-1")),
    ("https://a.wd3.myworkdayjobs.com/x/job/1", ("myworkday", None, None)),
])
def test_parse_url(url, exp):
    assert ats.parse_url(url) == exp


def test_keys_ignore_tracking():
    assert ats.job_key("https://www.a.com/j/1?utm_source=x&id=2") == ats.job_key("https://a.com/j/1/?id=2")
    assert ats.job_key("https://stripe.com/jobs/search?gh_jid=5") == \
        ats.job_key("https://job-boards.greenhouse.io/stripe/jobs/5") == "greenhouse:5"


def test_blocked_hosts():
    assert ats.is_blocked("https://www.linkedin.com/jobs/view/1") and ats.is_blocked("https://uk.indeed.com/x")
    assert not ats.is_blocked("https://notlinkedin.com/x")


def test_dedupe_and_progress_kept():
    with db.connect() as con:
        assert store(con, [job()], ALL)["new"] == 1
        # Same posting from another source: different key, same company/title/location.
        assert store(con, [job(key="k2", company="Acme Ltd")], ALL)["new"] == 0
        jid = con.execute("SELECT id FROM jobs").fetchone()["id"]
        db.set_status(con, jid, Status.shortlisted)
        store(con, [job()], ALL)
        assert db.get_job(con, jid)["status"] == "shortlisted"


def test_transitions():
    assert not can_move("discovered", "submitted") and not can_move("needs_review", "filling")
    assert can_move("approved", "filling") and can_move("submitted", "withdrawn")
    with db.connect() as con:
        store(con, [job()], ALL)
        with pytest.raises(db.BadTransition):
            db.set_status(con, 1, "offer")
        db.set_status(con, 1, "shortlisted")
        assert [e["to_status"] for e in con.execute("SELECT * FROM events ORDER BY id")] == ["discovered", "shortlisted"]


@pytest.mark.parametrize("locs,exp", [(["Singapore"], ["sg"]), (["SG - Singapore"], ["sg"]), (["Hong Kong; Shanghai; Singapore"], ["sg"]),
                                      (["London", "Singapore"], ["uk", "sg"]), (["New York"], []), (["London, UK"], ["uk"]), (["Sgurr"], [])])
def test_regions(locs, exp):
    assert filters.regions(locs) == exp


def test_country_switch():
    sg = job(locs=["Singapore"])
    assert filters.classify(sg, ALL, ["uk"])[1] == filters.OUTSIDE and filters.classify(sg, ALL, ["sg"])[1] is None
    with db.connect() as con:
        assert store(con, [sg], ALL, ["uk"])["new"] == 0 and store(con, [sg], ALL, ["uk", "sg"])["kept"] == 1


def test_boards_behind_company_sites(monkeypatch):
    from jobbot.sources import simplify
    rows = [{"url": u, "active": True, "company_name": "c", "title": "t", "locations": [], "date_posted": 0} for u in
            ("https://stripe.com/jobs/search?gh_jid=5", "https://www.tower-research.com/open?gh_jid=6", "https://jobs.lever.co/abc/1")]
    monkeypatch.setattr(simplify, "get_json", lambda c, u: rows)
    boards = simplify.fetch(None)[1]
    assert {("greenhouse", "stripe"), ("greenhouse", "tower-research"), ("greenhouse", "towerresearch"), ("lever", "abc"), ("ashby", "airwallex")} <= boards


def test_board_supplies_real_form():
    with db.connect() as con:
        j = RawJob(key="greenhouse:5", source="simplify", ats="greenhouse", company="Stripe", title="Software Engineer Intern", locations=["London, UK"],
                   url="https://stripe.com/jobs/search?gh_jid=5", apply_url="https://stripe.com/jobs/search?gh_jid=5")
        store(con, [j], ALL)
        real = "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=5"
        store(con, [j.model_copy(update={"apply_url": real, "source": "greenhouse"})], ALL)
        assert db.get_job(con, 1)["apply_url"] == real


def test_many_threads_at_once():
    from concurrent.futures import ThreadPoolExecutor

    def work(n):
        with db.connect() as con:
            store(con, [job(key=f"t{n}", title=f"Software Intern {n}")], ALL)
            return con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(work, range(80)))
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"] == 80
