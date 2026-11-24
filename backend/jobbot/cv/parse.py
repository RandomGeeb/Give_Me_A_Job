"""PDF to structured facts. The user confirms the result before anything is tailored from it."""
from pathlib import Path

from pypdf import PdfReader

from ..llm import client, prompts
from ..llm.schemas import ParsedCV


def with_ids(cv: dict) -> dict:
    for i, e in enumerate(cv["entries"], 1):
        e["id"] = f"e{i}"
        e["bullets"] = [{"id": f"e{i}.b{j}", "text": (b["text"] if isinstance(b, dict) else b).strip()}
                        for j, b in enumerate(e["bullets"], 1) if (b["text"] if isinstance(b, dict) else b).strip()]
    return cv


def parse_pdf(path: Path) -> dict:
    try:
        text = "\n\n".join(p.extract_text(extraction_mode="layout") or "" for p in PdfReader(str(path)).pages)
    except Exception as e:
        raise client.LLMError("This PDF could not be read.") from e
    if len(text.split()) < 60:
        raise client.LLMError("Little or no text was found in this PDF. If it is a scan or image, export a text PDF instead.")
    out = client.call("parse_cv", prompts.PARSE, "", client.tag("candidate_document", text) + "\n\nConvert this CV.", ParsedCV, effort="low")
    return with_ids(out.model_dump())


def flat(cv: dict) -> dict[str, str]:
    return {b["id"]: b["text"] for e in cv["entries"] for b in e["bullets"]}


def header(e: dict) -> str:
    return " ".join([e["title"], e["organisation"], e["location"], e["dates"]])


def all_text(cv: dict) -> str:
    parts = [cv["name"]]
    for e in cv["entries"]:
        parts += [header(e)] + [b["text"] for b in e["bullets"]]
    parts += [f"{s['group']}: {', '.join(s['items'])}" for s in cv["skills"]]
    return "\n".join(parts)


def context(cv: dict, profile: dict) -> str:
    """Candidate context sent to the model. Contact details and sensitive answers are left out."""
    facts = {"entries": cv["entries"], "skills": cv["skills"]}
    keep = ("university", "degree", "discipline", "degree_level", "grade", "study_start", "graduation", "city", "country",
            "availability", "notice", "extra_answers")
    rights = {"right_to_work_by_country": {
        "United Kingdom": {"status": profile["work_rights"], "needs_sponsorship_now_or_later": profile["needs_sponsorship_uk"]},
        "Singapore": {"status": profile["work_rights_sg"], "needs_sponsorship_now_or_later": profile["needs_sponsorship_sg"]}}}
    return client.tag("candidate_facts", facts) + "\n" + \
        client.tag("candidate_profile", {k: profile[k] for k in keep if profile.get(k) not in ("", None, [])} | rights)
