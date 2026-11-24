"""Independent review plus the approval hash."""
import hashlib
import json
from pathlib import Path

from .cv import verify
from .cv.parse import context
from .cv.tailor import job_block
from .llm import client, prompts
from .llm.schemas import Review


def needs_check(a: dict) -> bool:
    """Any free text the model wrote, whatever source it claims. Your own edits are exempt."""
    return bool(a["value"]) and not a["options"] and a["basis"] not in ("contact", "unknown") and not a.get("edited")


def code_checks(job, cv, profile, app, pages: int | None) -> list[dict]:
    out = verify.check_cv(app["cv"], cv)
    if pages and pages > 1:
        out.append({"severity": "block", "where": "CV", "problem": f"The PDF is {pages} pages.",
                    "suggestion": "Remove bullets until it fits one page.", "by": "code"})
    if app.get("cover_letter"):
        out += verify.check_letter(app["cover_letter"], cv, profile, job, app.get("research") or (), app.get("letter_notes") or "")
    for a in (app.get("answers") or {}).values():
        if needs_check(a):
            out += verify.check_prose(f"Answer: {a['label'][:60]}", " ".join(a["value"]), cv, profile, job)
    return out


REUSED = (' The CV was written for a similar role. If it leaves out an essential requirement of this posting that the facts support, '
          'report it as "warn" with `where` exactly "CV fit", naming the fact to add. Report nothing there for optional requirements or wording.')


def ai_review(job, cv, profile, app, reused: bool = False) -> dict:
    entries = {e["id"]: e for e in cv["entries"]}
    final = {"cv": [{"entry": entries[e["entry_id"]]["title"], "organisation": entries[e["entry_id"]]["organisation"],
                     "bullets": [b["text"] for b in e["bullets"]]} for e in app["cv"]["entries"] if e["entry_id"] in entries],
             "skills": app["cv"]["skills"], "cover_letter": app.get("cover_letter") or "",
             "answers": [{"question": a["label"], "answer": a["value"]} for a in (app.get("answers") or {}).values()
                         if a["value"] and a["basis"] != "contact"]}
    r = client.call("review", prompts.REVIEW, context(cv, profile),
                    job_block(job) + "\n\n" + client.tag("company_research", [{k: f[k] for k in ("id", "quote", "published")} for f in app.get("research") or []])
                    + ("\n\n" + client.tag("candidate_reasons", app["letter_notes"]) if app.get("letter_notes") else "")
                    + "\n\n" + client.tag("final_application", final) + "\n\nReview it." + (REUSED if reused else ""),
                    Review, effort="high", job_id=job["id"]).model_dump()
    for i in r["issues"]:
        i["by"] = "AI"
    if r["employer_restricts_ai"]:
        r["issues"].insert(0, {"severity": "block", "where": "Employer rule", "by": "AI",
                               "problem": f"The employer appears to limit AI use: \"{r['ai_rule_quote'][:300]}\"",
                               "suggestion": "Read the rule. Write this application yourself if AI help is not allowed."})
    return r


def content_hash(app: dict) -> str:
    """Covers everything that will be sent, including the PDF bytes."""
    pdf = Path(app["pdf_path"]) if app.get("pdf_path") else None
    body = {"cv": app.get("cv"), "cover": app.get("cover_letter") or "",
            # Contact details come from the profile at fill time, so they are not part of the approved text.
            "answers": {k: a["value"] for k, a in (app.get("answers") or {}).items() if a["value"] and a["basis"] != "contact"},
            "pdf": hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf and pdf.exists() else ""}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def is_approved(app: dict | None) -> bool:
    return bool(app and app.get("approved_hash") and app["approved_hash"] == content_hash(app))


def blockers(app: dict) -> list[dict]:
    return [i for i in (app.get("review") or {}).get("issues", []) if i["severity"] == "block"]
