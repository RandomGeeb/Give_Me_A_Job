"""Field descriptions from a page, stable keys, and answers that come straight from the profile."""
import re
from pathlib import Path

from . import policy

JS = (Path(__file__).parent / "extract.js").read_text()
norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def add_keys(raw: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    out = []
    for f in raw:
        if f["kind"] != "file" and policy.NOT_APPLICATION.search(f["label"]):
            continue
        base = f"{norm(f['label'])[:120]}|{'choice' if f['kind'] in ('radio', 'yesno', 'select', 'combobox') else f['kind']}"
        seen[base] = seen.get(base, 0) + 1
        f["key"] = base if seen[base] == 1 else f"{base}#{seen[base]}"
        f["opts"] = [o["label"] for o in f["options"]]
        f["policy"] = policy.classify(f["label"], f["kind"], f["opts"])
        out.append(f)
    return out


def looks_like_application(fs: list[dict]) -> bool:
    """A CV upload, or at least a name and an email."""
    labels = [norm(f["label"]) for f in fs]
    cv = any(f["kind"] == "file" and re.search(r"resume|\bcv\b|curriculum", norm(f["label"] + " " + f.get("hint", ""))) for f in fs)
    return cv or (any(re.search(r"\bname\b", x) for x in labels) and any("email" in x or "e mail" in x for x in labels))


DIRECT = [
    (r"^(legal )?(first|given) name|^forename", "first_name"), (r"^(legal )?(last|family) name( surname)?$|^surname|^last name", "last_name"),
    (r"^preferred (first )?name", "preferred_name"), (r"^(full |your |legal )*(full )?name$", "_full"),
    (r"^e ?mail( address)?$|^your email", "email"), (r"^(mobile|phone|telephone|contact)( phone)?( number)?$", "phone"),
    (r"linkedin", "linkedin"), (r"^github|github (url|profile|link)", "github"), (r"^(personal )?(website|portfolio)", "website"),
    (r"^(current )?(location|city)( city)?$|^where are you (currently )?(based|located)$", "city"),
    (r"^(current )?country( of residence)?$", "country"),
    (r"^(school|university|institution)$|^university (name|institution|attended)", "university"),
]
DIRECT = [(re.compile(p), k) for p, k in DIRECT]


def from_profile(f: dict, profile: dict) -> dict | None:
    """Contact details and other facts where no judgement is needed."""
    if f["policy"] != "ok":
        return None
    n = norm(f["label"])
    for rx, key in DIRECT:
        if rx.search(n):
            v = f"{profile['first_name']} {profile['last_name']}".strip() if key == "_full" else profile.get(key)
            if not v:
                return {"value": [], "basis": "unknown", "note": "Not in your profile."}
            if f["opts"]:
                m = match_option(v, f["opts"])
                return {"value": [m] if m else [], "basis": "contact" if m else "unknown", "note": ""}
            return {"value": [v], "basis": "contact", "note": ""}
    for qa in profile.get("extra_answers") or []:
        if norm(qa["question"]) == n and qa["answer"]:
            m = match_option(qa["answer"], f["opts"]) if f["opts"] else qa["answer"]
            if m:
                return {"value": [m], "basis": "contact", "note": "From your saved answers."}
    return None


def match_option(value: str, options: list[str]) -> str | None:
    v = norm(value)
    for o in options:
        if norm(o) == v:
            return o
    starts = [o for o in options if norm(o).startswith(v + " ")]
    if len(starts) == 1:
        return starts[0]
    # Place pickers: "London" should mean London, England, not London, Ontario.
    uk = [o for o in options if norm(o.split(",")[0]) == v and norm(o).endswith("united kingdom")]
    return uk[0] if len(uk) == 1 else None


def plan(fs: list[dict], profile: dict) -> tuple[dict[str, dict], list[dict]]:
    """(answers known without the model, fields to ask the model about)."""
    known, ask = {}, []
    for f in fs:
        base = {"label": f["label"], "kind": f["kind"], "required": bool(f["required"]), "options": f["opts"][:80], "policy": f["policy"]}
        p = f["policy"]
        if p.startswith("sensitive:"):
            v = (profile.get("sensitive") or {}).get(p.split(":")[1])
            m = (match_option(v, f["opts"]) if f["opts"] else v) if v else None
            # Stored answers are applied, but never placed in anything sent to the model.
            known[f["key"]] = {**base, "value": [m] if m else [], "basis": "contact" if m else "unknown",
                               "note": "From your saved personal answers." if m else "Personal question: yours to answer."}
        elif p == "consent":
            known[f["key"]] = {**base, "value": [], "basis": "unknown", "note": "Declaration: read and tick it yourself."}
        elif p == "ai_rule":
            known[f["key"]] = {**base, "value": [], "basis": "unknown", "note": "This question limits AI use: write it yourself."}
        elif p == "file":
            continue
        elif (a := from_profile(f, profile)) is not None:
            known[f["key"]] = {**base, **a}
        else:
            ask.append({**f, "options": f["opts"], "base": base})
    return known, ask


def merge(ask: list[dict], drafted: dict[str, dict]) -> dict[str, dict]:
    return {f["key"]: {**f["base"], **drafted[f["key"]]} for f in ask if f["key"] in drafted}
