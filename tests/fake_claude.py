#!/usr/bin/env python3
"""Stands in for the claude program. What it does is set by JB_FAKE; what it was given is written to JB_LOG."""
import json
import os
import sys
import time

if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps(json.loads(os.environ.get("JB_AUTH", '{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "pro"}'))))
    sys.exit()
mode = os.environ.get("JB_FAKE", "ok")
a = sys.argv[1:]
system = open(a[a.index("--system-prompt-file") + 1]).read()
with open(os.environ["JB_LOG"], "a") as f:
    f.write(json.dumps({"args": a, "system": system, "stdin": sys.stdin.read(), "cwd": os.listdir("."), "env": sorted(os.environ)}) + "\n")
out = lambda **e: print(json.dumps(e), flush=True)
out(type="system", subtype="init", apiKeySource="ANTHROPIC_API_KEY" if mode == "key" else "none",
    tools=["Bash"] if mode == "tools" else ["WebSearch"] if "WebSearch" in a else [])
if mode == "crash":
    sys.exit("Not logged in. Please run /login")
out(type="rate_limit_event", rate_limit_info={"isUsingOverage": mode == "overage", "unifiedWindows": {
    "five_hour": {"utilization": 0.85 if mode == "nearly" else 0.4, "resetsAt": time.time() + 3600}, "seven_day": {"utilization": 0.1, "resetsAt": time.time() + 9e4}}})
if "WebSearch" in a:
    for n in range(6):
        out(type="assistant", message={"content": [{"type": "tool_use", "id": f"t{n}", "name": "WebSearch", "input": {}}]})
        body = "Web search results for query: \"q\"\n\nLinks: " + json.dumps([{"title": "a", "url": f"https://a{n}.test/"}, {"title": "x", "url": "javascript:1"}])
        out(type="user", message={"content": [{"type": "tool_result", "tool_use_id": f"t{n}", "content": body + "\n\nLinks: [{\"url\": \"https://prose.test/\"}]"}]})
        out(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "other", "content": body.replace("a0", "unasked")}]})
    out(type="assistant", message={"content": [{"type": "text", "text": "see https://invented.test"}]})
good = {"is_relevant_internship": True, "family": "software", "eligible": "yes", "eligibility_notes": [], "dates": "fit", "requirements": [], "competition": "normal",
        "deadline": None, "application_limit": None, "summary": "ok"}
r = {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn", "result": "ok", "usage": {"input_tokens": 5, "output_tokens": 7}}
if "--json-schema" in a:
    r["structured_output"] = {"family": 1} if mode == "bad" else good
if mode == "refusal":
    r["stop_reason"] = "refusal"
if mode == "limit":
    r |= {"is_error": True, "result": "You have hit your usage limit", "api_error_status": 429}
if mode == "nothing":
    r.pop("structured_output", None)
out(**r)
