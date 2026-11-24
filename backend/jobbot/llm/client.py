"""Every model call goes through your own Claude Code login (`claude -p`), never an API key.

Each call runs in an empty folder with no tools, except web search when finding roles, so text in a job posting has
nothing to act with.
"""
import functools
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .. import costs, db

T = TypeVar("T", bound=BaseModel)
TIMEOUT = 600
KEEP_ENV = {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN"}
_slots = threading.Semaphore(2)


class LLMError(RuntimeError):
    pass


def binary() -> str | None:
    local = Path.home() / ".local/bin/claude"
    return shutil.which("claude") or (str(local) if local.exists() else None)


def env() -> dict[str, str]:
    """Without API keys or another session's settings, which would override the login."""
    return {k: v for k, v in os.environ.items() if k in KEEP_ENV or not k.startswith(("ANTHROPIC_", "CLAUDE"))}


@functools.lru_cache(1)
def _status(_bust: int) -> dict:
    b = binary()
    if not b:
        return {"ok": False, "detail": "Claude Code is not installed."}
    try:
        p = subprocess.run([b, "auth", "status"], capture_output=True, text=True, env=env(), timeout=20)
        d = json.loads(p.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ok": False, "detail": "Could not check the Claude Code login. Run claude in a terminal."}
    if not d.get("loggedIn") or d.get("authMethod") != "claude.ai":
        return {"ok": False, "detail": "Claude Code is not signed in with a Claude plan. Run claude auth login in a terminal."}
    return {"ok": True, "detail": d.get("subscriptionType") or "signed in"}


def status() -> dict:
    import time
    return _status(int(time.time() // 120))


def strict(schema: dict) -> dict:
    """Every object closed and every property required."""
    if isinstance(schema, dict):
        out = {k: strict(v) for k, v in schema.items() if k not in ("title", "default")} if "properties" not in schema else {
            **{k: strict(v) for k, v in schema.items() if k not in ("title", "default", "properties")},
            "properties": {k: strict(v) for k, v in schema["properties"].items()},
            "required": list(schema["properties"]), "additionalProperties": False}
        return out
    return [strict(x) for x in schema] if isinstance(schema, list) else schema


def _explain(text: str, api_status=None) -> str:
    low = (text or "").lower()
    if "login" in low or "not logged in" in low or api_status == 401:
        return "Claude Code is not signed in. Run claude auth login in a terminal, then try again."
    if "limit" in low or api_status == 429:
        return "Your Claude plan has reached its usage limit. Try again after it resets."
    if api_status == 529 or "overloaded" in low:
        return "Claude is busy; try again shortly."
    return f"Claude Code reported a problem: {(text or 'no details')[:200]}"


def _run(purpose: str, system: str, prompt: str, *, effort: str, job_id=None, schema: dict | None = None,
         searches: int = 0) -> tuple[dict, list[dict]]:
    """Returns (final result event, search links)."""
    with _slots:
        with db.connect() as con:
            costs.check(con)
            model = db.settings(con)["model"]
        b = binary()
        if not b:
            raise LLMError("Claude Code is not installed. Install it, sign in, then try again.")
        with tempfile.TemporaryDirectory(prefix="jobbot-") as d:     # created readable by this user only
            work = Path(d) / "empty"
            work.mkdir()
            sysfile = Path(d) / "system.txt"
            sysfile.write_text(system)
            cmd = [b, "-p", "--model", model, "--effort", effort, "--output-format", "stream-json", "--verbose",
                   "--system-prompt-file", str(sysfile), "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
                   "--permission-prompts", "none", "--tools", "WebSearch" if searches else ""]
            if searches:
                cmd += ["--allowedTools", "WebSearch"]
            if schema:
                cmd += ["--json-schema", json.dumps(schema)]
            return _stream(cmd, work, prompt, purpose, job_id, model, searches)


def _stream(cmd, work, prompt, purpose, job_id, model, searches):
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env(), cwd=work)
    timed_out = threading.Event()
    timer = threading.Timer(TIMEOUT, lambda: (timed_out.set(), p.kill()))
    timer.start()
    err: list[str] = []
    threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True).start()

    def feed():
        try:
            p.stdin.write(prompt)
            p.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
    threading.Thread(target=feed, daemon=True).start()
    result, links, asked, problem = None, {}, {}, None
    try:
        for line in p.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            kind = e.get("type")
            if kind == "system" and e.get("subtype") == "init":
                if e.get("apiKeySource") not in (None, "none"):
                    problem = "Claude Code tried to use an API key instead of your login, so the call was stopped."
                extra = set(e.get("tools") or []) - {"WebSearch", "StructuredOutput"}
                if extra:
                    problem = f"Claude Code offered unexpected tools ({', '.join(sorted(extra))[:80]}), so the call was stopped."
            elif kind == "rate_limit_event":
                info = e.get("rate_limit_info") or {}
                with db.connect() as con:
                    costs.save_plan(con, info)
                if info.get("isUsingOverage"):
                    problem = "Your plan's included usage has run out and this would use paid extra usage, so the call was stopped."
            elif kind == "assistant":
                for c in e["message"]["content"]:
                    if c["type"] == "tool_use" and c["name"] == "WebSearch":
                        asked[c["id"]] = True
            elif kind == "user" and isinstance(e["message"]["content"], list):
                for c in e["message"]["content"]:
                    if c.get("type") == "tool_result" and c.get("tool_use_id") in asked:
                        for x in search_links(c.get("content")):
                            links.setdefault(x["url"], x)
                        asked[c["tool_use_id"]] = False
                # Enough searches: stop here, since only the links are wanted.
                if searches and len(asked) >= searches and not any(asked.values()):
                    break
            elif kind == "result":
                result = e
            if problem:
                break
    finally:
        timer.cancel()
        if p.poll() is None:
            if result is None:
                p.terminate()
            try:
                p.wait(15)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)
        with db.connect() as con:
            costs.record(con, purpose, job_id, model, (result or {}).get("usage") or {})
    if problem:
        raise LLMError(problem)
    if timed_out.is_set():
        raise LLMError(f"Claude took longer than {TIMEOUT // 60} minutes, so the call was stopped.")
    if result is None:
        if searches and links:
            return {}, list(links.values())
        raise LLMError(_explain((err[0] if err else "").strip()))
    if result.get("stop_reason") == "refusal":
        raise LLMError("Claude declined this request.")
    if result.get("is_error") or result.get("subtype") != "success":
        raise LLMError(_explain(result.get("result") or result.get("subtype"), result.get("api_error_status")))
    return result, list(links.values())


def search_links(content) -> list[dict]:
    """The link list the search tool attached to its result. Anything in the prose after it is ignored."""
    if isinstance(content, list):
        content = "\n".join(x.get("text", "") for x in content if isinstance(x, dict))
    head, mark, rest = (content or "").partition("Links: ")
    if not mark or not head.startswith("Web search results for query:"):
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(rest)
    except ValueError:
        return []
    return [{"url": x["url"], "title": x.get("title") or ""} for x in data
            if isinstance(x, dict) and str(x.get("url", "")).startswith(("http://", "https://"))]


def web_search(prompt: str, system: str, max_uses: int, *, purpose: str = "web_search", job_id: int | None = None) -> list[dict]:
    """Links exactly as the search tool returned them. The model's own text is ignored, so it cannot invent one."""
    return _run(purpose, system, prompt, effort="low", job_id=job_id, searches=max_uses)[1]


def call(purpose: str, system: str, shared: str, content: str, out: type[T], *, effort: str = "medium", job_id: int | None = None) -> T:
    """`shared` is the candidate context; `content` is specific to this call."""
    r, _ = _run(purpose, system + ("\n\n" + shared if shared else ""), content, effort=effort, job_id=job_id,
                schema=strict(out.model_json_schema()))
    data = r.get("structured_output")
    if data is None:
        raise LLMError(f"No structured output for {purpose}.")
    try:
        return out.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Unreadable model output for {purpose}.") from e


def tag(name: str, body) -> str:
    """Wrap untrusted text so it cannot close its own tag."""
    s = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, sort_keys=True, indent=1)
    return f"<{name}>\n{s.replace('</' + name, '< /' + name)}\n</{name}>"
