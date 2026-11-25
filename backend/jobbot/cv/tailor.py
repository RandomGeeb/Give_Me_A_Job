from datetime import date

from ..llm import client, prompts
from ..llm.schemas import Answers, CoverLetter, TailoredCV
from ..apply import policy
from ..apply.fields import match_option
from .parse import context

MAX_OPTIONS = 80
KINDS = {"dropdown": "select", "multiselect": "select", "dateparts": "date"}


def job_block(job: dict) -> str:
    return client.tag("job", f"Company: {job['company']}\nTitle: {job['title']}\n"
                             f"Locations: {', '.join(job['locations'] or [])}\n\n{job['description'] or '(no description available)'}")


def _fix(issues) -> str:
    if not issues:
        return ""
    return "\n\nA reviewer rejected the previous version. Fix these and nothing else:\n" + \
        "\n".join(f"- {i['where']}: {i['problem']} {i['suggestion']}" for i in issues)


def tailor(job, cv, profile, prev=None, issues=None) -> dict:
    msg = job_block(job) + "\n\nTailor the CV to this job."
    if prev:
        msg += "\n\n" + client.tag("previous_version", prev) + _fix(issues)
    return client.call("tailor", prompts.TAILOR, context(cv, profile), msg, TailoredCV,
                       effort="high", job_id=job["id"]).model_dump()


def cover_letter(job, cv, profile, research=(), notes="", prev=None, issues=None) -> tuple[str, list[str]]:
    """Returns the letter and the ids of the research passages it relies on."""
    msg = job_block(job) + "\n\n" + client.tag("company_research", [{k: f[k] for k in ("id", "quote", "published", "own_site")} for f in research])
    if notes:
        msg += "\n\n" + client.tag("candidate_reasons", notes)
    msg += f"\n\nToday is {date.today().isoformat()}. Write the cover letter body."
    if prev:
        msg += "\n\n" + client.tag("previous_version", prev) + _fix(issues)
    out = client.call("cover", prompts.COVER, context(cv, profile), msg, CoverLetter, effort="high", job_id=job["id"])
    cited = {i for p in out.paragraphs for i in p.finding_ids}
    return "\n\n".join(p.text.strip() for p in out.paragraphs), [f["id"] for f in research if f["id"] in cited]


def draft_answers(job, cv, profile, fields: list[dict], prev=None, issues=None, others=()) -> dict[str, dict]:
    """others: roles at this employer already applied for. fields: [{key,label,kind,required,options}] already cleared by apply.policy."""
    if not fields:
        return {}
    ids = {f"f{i}": f for i, f in enumerate(fields)}
    form = [{"id": i, "question": f["label"], "kind": KINDS.get(f["kind"], f["kind"]), "required": f["required"],
             "options": f["options"] if len(f["options"]) <= MAX_OPTIONS else
             ["(too many to list: give the usual full name and it will be matched)"],
             **({"options": ["(searched as you type: give the usual full name)"]} if f["kind"] == "multiselect" and not f["options"] else {}),
             **({"max_characters": f["maxlength"]} if f.get("maxlength") else {})} for i, f in ids.items()]
    msg = job_block(job) + "\n\n" + client.tag("other_applications", list(others) or "None to this employer so far.") + \
        "\n\n" + client.tag("form", form) + "\n\nPrepare the answers."
    if prev:
        msg += "\n\n" + client.tag("previous_version", prev) + _fix(issues)
    res = client.call("answers", prompts.ANSWER, context(cv, profile), msg, Answers, effort="medium", job_id=job["id"])
    out = {}
    for a in res.answers:
        f = ids.get(a.field)
        if f:
            out[f["key"]] = {**clean(f, a.model_dump()), "tried": True}
    for f in fields:
        out.setdefault(f["key"], {"value": [], "basis": "unknown", "note": "No answer returned.", "tried": True})
    return out


def clean(f: dict, a: dict) -> dict:
    """Never trust a choice that is not on the form."""
    val = [v for v in a["value"] if v.strip()]
    if a["basis"] == "unknown":
        val = []
    if a["basis"] == "guess" and policy.LEGAL.search(f.get("label", "")):
        return {"value": [], "basis": "unknown", "note": "A statement only you can make: answer it yourself."}
    if f["kind"] == "multiselect":
        val = val[:1]
    if len(f["options"]) > MAX_OPTIONS:
        val = [m for v in val[:1] if (m := match_option(v, f["options"]))]
    elif f["options"]:
        ok = [m for v in val if (m := v if v in f["options"] else match_option(v, f["options"]))]
        if len(ok) != len(val):
            return {"value": [], "basis": "unknown", "note": "Suggested option is not on the form."}
        val = ok if f["kind"] == "checkbox" else ok[:1]
    return {"value": val, "basis": a["basis"] if val else "unknown", "note": a["note"]}
