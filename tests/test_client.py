import json
from pathlib import Path

import pytest

from jobbot import costs, db, scoring
from jobbot.llm import client
from test_writing import CV, JOB, PROFILE

FAKE = str(Path(__file__).parent / "fake_claude.py")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    log = tmp_path / "log"
    monkeypatch.setattr(client, "binary", lambda: FAKE)
    monkeypatch.setenv("JB_LOG", str(log))
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDECODE"):
        monkeypatch.setenv(k, "leak")
    client._status.cache_clear()
    return lambda: [json.loads(x) for x in log.read_text().splitlines()]


def score():
    return scoring.score_job(JOB, CV, PROFILE)


def test_call_is_isolated(fake):
    assert score()["eligible"] == "yes"
    c = fake()[0]
    a = c["args"]
    assert a[a.index("--tools") + 1] == "" and "--allowedTools" not in a and a[a.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in a and "--no-session-persistence" in a and a[a.index("--model") + 1] == "claude-opus-5-5"
    assert c["cwd"] == [] and not [k for k in c["env"] if k.startswith(("ANTHROPIC", "CLAUDE"))]
    assert "candidate_facts" in c["system"] and "Kubernetes" in c["stdin"]
    # Nothing personal on the command line, where other programs could see it, and nothing private anywhere.
    assert "Acme" not in " ".join(a) and "SECRET-GENDER" not in json.dumps(c) and "s@x.test" not in json.dumps(c)
    with db.connect() as con:
        assert costs.calls_today(con) == 1 and costs.plan(con)["five_hour"]["utilization"] == 0.4


@pytest.mark.parametrize("mode,msg", [("key", "API key instead of your login"), ("tools", "unexpected tools"), ("overage", "paid extra usage"),
                                      ("crash", "not signed in"), ("refusal", "declined"), ("limit", "usage limit"), ("bad", "Unreadable"),
                                      ("nothing", "No structured output")])
def test_failures_are_explained(fake, monkeypatch, mode, msg):
    monkeypatch.setenv("JB_FAKE", mode)
    with pytest.raises(client.LLMError, match=msg):
        score()
    with db.connect() as con:
        assert costs.calls_today(con) == 1      # failed calls count too


def test_stops_before_plan_runs_out(fake, monkeypatch):
    monkeypatch.setenv("JB_FAKE", "nearly")
    score()
    with pytest.raises(costs.LimitReached, match="5-hour limit is 85% used"):
        score()
    assert len(fake()) == 1
    with db.connect() as con:      # once that window resets, calls resume
        db.put_setting(con, "plan_usage", {"five_hour": {"utilization": .85, "resetsAt": 1}})
    score()


def test_daily_limit(fake):
    with db.connect() as con:
        db.put_setting(con, "daily_calls", 1)
    score()
    with pytest.raises(costs.LimitReached, match="Daily limit"):
        score()


def test_no_daily_limit_and_usage(fake):
    with db.connect() as con:
        db.put_setting(con, "daily_calls", 0)
        db.put_setting(con, "plan_usage", {"five_hour": {"utilization": .6, "resetsAt": 1}, "seven_day": {"utilization": .2, "resetsAt": 9e9}})
        costs.check(con, 500)
        u = costs.usage(con)
    assert u["five_hour"]["used"] is None and u["seven_day"]["used"] == .2
    score()
    with db.connect() as con:
        assert costs.usage(con)["five_hour"]["used"] == .4 and costs.usage(con)["five_hour"]["at"]


def test_search_links_only_from_results(fake):
    got = [x["url"] for x in client.web_search("p", "s", 3)]
    assert got == ["https://a0.test/", "https://a1.test/", "https://a2.test/"]
    a = fake()[0]["args"]
    assert a[a.index("--tools") + 1] == "WebSearch" and "--json-schema" not in a


def test_timeout(fake, monkeypatch, tmp_path):
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nexec sleep 30\n")
    slow.chmod(0o755)
    monkeypatch.setattr(client, "binary", lambda: str(slow))
    monkeypatch.setattr(client, "TIMEOUT", 0.5)
    with pytest.raises(client.LLMError, match="longer than"):
        score()


def test_not_installed(monkeypatch):
    client._status.cache_clear()
    monkeypatch.setattr(client, "binary", lambda: None)
    with pytest.raises(client.LLMError, match="not installed"):
        score()
    assert not client.status()["ok"]


@pytest.mark.parametrize("auth,ok", [('{"loggedIn": true, "authMethod": "claude.ai"}', True), ('{"loggedIn": false}', False),
                                     ('{"loggedIn": true, "authMethod": "api_key"}', False)])
def test_login_status(fake, monkeypatch, auth, ok):
    monkeypatch.setenv("JB_AUTH", auth)
    assert client.status()["ok"] is ok


def test_schema_is_closed():
    from jobbot.llm.schemas import TailoredCV
    s = client.strict(TailoredCV.model_json_schema())
    assert s["additionalProperties"] is False and all(d["additionalProperties"] is False for d in s["$defs"].values())


def test_scanned_cv_refused(tmp_path):
    from pypdf import PdfWriter
    from jobbot.cv import parse
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.write(tmp_path / "x.pdf")
    with pytest.raises(client.LLMError, match="Little or no text"):
        parse.parse_pdf(tmp_path / "x.pdf")
