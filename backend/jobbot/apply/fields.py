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
        base = f"{norm(f['label'])[:120]}|{'choice' if f['kind'] in ('radio', 'yesno', 'select', 'combobox', 'dropdown', 'multiselect') else f['kind']}"
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
    (r"^(legal )?(first|given)( name)? (and |& )?(last|family|surname)( name)?$", "_full"),
    (r"^(legal )?(first|given) name|^forename", "first_name"), (r"^(legal )?(last|family) name( surname)?$|^surname|^last name", "last_name"),
    (r"^preferred (first )?name", "preferred_name"), (r"^(full |your |legal )*(full )?name$", "_full"),
    (r"^e ?mail( address)?$|^your email", "email"), (r"^(mobile|phone|telephone|contact)( phone)?( number)?$", "phone"),
    (r"linkedin", "linkedin"), (r"^github|github (url|profile|link)", "github"), (r"^(personal )?(website|portfolio)", "website"),
    (r"^(current )?(location|city)( city)?$|^where are you (currently )?(based|located)$", "city"),
    (r"^(current )?country( of residence)?$", "country"), (r"^(city|town)( or (city|town))?$", "city"),
    (r"^(address( line)?( 1)?|street( address)?|home address)$", "address"), (r"^(post ?code|postal code|zip( code)?|zip or postal code)$", "postcode"),
    (r"^(school|university|institution)$|^university (name|institution|attended)", "university"),
]
DIRECT = [(re.compile(p), k) for p, k in DIRECT]


DIAL = {"+44": "United Kingdom", "+65": "Singapore"}
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}

FULL = "January February March April May June July August September October November December".split()


def month(s: str) -> str:
    """'Sep 2025' or 'September 2025' as 2025-09; a year alone stays a year."""
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", s or "")
    if m and m[1].lower() in MONTHS:
        return f"{m[2]}-{MONTHS[m[1].lower()]:02d}"
    y = re.search(r"\b(19|20)\d\d\b", s or "")
    return y[0] if y else ""


def day(s: str) -> str:
    """'30 June 2028' as 2028-06-30. Blank unless the day is given."""
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?=[A-Za-z]{3,9}\.?\s+\d{4})", s or "")
    ym = month(s)
    return f"{ym}-{int(m[1]):02d}" if m and len(ym) == 7 and 1 <= int(m[1]) <= 31 else ""


def dates(s: str) -> tuple[str, str, bool]:
    """(start, end, still there)"""
    a, _, b = re.sub(r"\s+(to|until)\s+", "–", s or "").replace("—", "–").replace(" - ", "–").partition("–")
    return month(a), month(b), bool(re.search(r"present|current|now|ongoing", b, re.I))


def worked(tailored: dict, cv: dict) -> list[tuple[dict, list[str]]]:
    """Jobs on the approved CV, in its order, with their approved bullets."""
    by = {e["id"]: e for e in cv["entries"]}
    return [(by[t["entry_id"]], [b["text"] for b in t["bullets"]]) for t in tailored["entries"]
            if t["entry_id"] in by and by[t["entry_id"]]["kind"] == "experience"]


WORK = re.compile(r"^(work |professional |employment )?(experience|history|employment)\b", re.I)


def calendar(n: str, start: str, end: str) -> str | None:
    """Month and year in boxes of their own ("Start date month"). None if this is not one of those boxes."""
    m = re.fullmatch(r"(start|end|from|to)( date)? (month|year)", n)
    if not m:
        return None
    y, _, mo = (start if m[1] in ("start", "from") else end).partition("-")
    return y if m[3] == "year" else FULL[int(mo) - 1] if mo else ""


def from_cv(f: dict, tailored: dict, cv: dict, profile: dict) -> dict | None:
    """Work history, education dates and skills that a site asks for in separate boxes: copied from the CV you approved."""
    say = lambda v: {"value": [x for x in v if x], "basis": "contact" if any(v) else "unknown", "note": "From your approved CV." if any(v) else "Not on your CV."}
    n = norm(f.get("name") or f["label"])
    if f["kind"] == "multiselect" and re.fullmatch(r"(type to add )?skills", n):
        return say([x for g in tailored["skills"] for x in g["items"]][:12])
    m = re.match(r"(.*?) (\d+)$", f.get("section") or "")
    if not m:
        return None
    if WORK.match(m[1]):
        jobs = worked(tailored, cv)
        if not 0 < int(m[2]) <= len(jobs):
            return say([])          # never another made-up job
        e, bullets = jobs[int(m[2]) - 1]
        start, end, still = dates(e["dates"])
        if re.search(r"currently work|current (job|role|position)", n):
            return say(f["opts"][:1] if still else []) | ({} if still else {"note": ""})
        if still and re.search(r"^(to|end date|end)\b", n):
            return say([]) | {"note": ""}
        if (v := calendar(n, start, end)) is not None:
            return say([v])
        for rx, v in ((r"job title|^title|position|^role$", e["title"]), (r"company|employer|organi[sz]ation", e["organisation"]), (r"^location", e["location"]),
                      (r"description|responsibilit|duties", "\n".join("• " + b for b in bullets)), (r"^(from|start)", start), (r"^(to|end)\b", end)):
            if re.search(rx, n):
                return say([v])
    if re.match(r"education", m[1], re.I) and m[2] == "1":
        if (v := calendar(n, month(profile.get("study_start")), month(profile.get("graduation")))) is not None:
            return say([v])
        for rx, v in ((r"school|university|institution|college", profile.get("university")), (r"^(from|start|first year)", month(profile.get("study_start"))),
                      (r"^(to|end|last year|graduat|expected)", month(profile.get("graduation"))), (r"^(field of study|major|subject|discipline)", profile.get("discipline"))):
            if re.search(rx, n):
                # A list that words it differently needs a choice made, so that is left to the usual answers.
                return None if f["kind"] == "combobox" and (not v or f["opts"] and not match_option(v, f["opts"])) else say([v])
    return None


def from_profile(f: dict, profile: dict) -> dict | None:
    """Contact details and other facts where no judgement is needed."""
    if f["policy"] != "ok":
        return None
    n = norm(f["label"])
    if f["opts"] and re.search(r"^phone (device )?type$", n):
        m = next((o for o in f["opts"] if re.search(r"mobile|cell", o, re.I)), None)
        return {"value": [m] if m else [], "basis": "contact" if m else "unknown", "note": ""}
    if re.search(r"^(country )?(phone|dial\w*) code$|^country phone code$", n):
        place = DIAL.get(profile.get("_dial"))
        return {"value": [place] if place else [], "basis": "contact" if place else "unknown", "note": ""}
    if re.fullmatch(r"title|prefix|salutation|phone extension|address line [23]", n):
        return {"value": [], "basis": "unknown", "note": "Yours to choose."}
    if f["kind"] in ("date", "dateparts") and re.search(r"graduat|(degree|course|stud(y|ies)) (end|completion|finish)|(complet|finish)\w* (your |the )?(degree|course|studies)", n):
        # Only the boxes the form has: a date with no day is enough for a month and year.
        v = profile.get("graduation") or ""
        v = day(v) if f["kind"] == "date" or "day" in f.get("parts", []) else month(v)
        return {"value": [v], "basis": "contact", "note": "Your graduation date."} if v else \
            {"value": [], "basis": "unknown", "note": "Your profile has no exact graduation day."}
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


DECLINE = re.compile(r"prefer not|rather not|do(n.?t| not) (wish|want)|decline|not to (say|answer|state|disclose)", re.I)
SAME = {"gender": [{"man", "male", "m"}, {"woman", "female", "f"}, {"non binary", "nonbinary"}],
        "pronouns": [{"he him", "he him his"}, {"she her", "she her hers"}, {"they them", "they them theirs"}],
        "disability": [{"no", "none", "nil", "n a", "no disability", "no disabilities", "not disabled", "i do not have a disability"}]}


def match_personal(kind: str, value: str, label: str, options: list[str]) -> str | None:
    """The option that means the same as a saved personal answer. Code only, so it never leaves this Mac; one match or none."""
    one = lambda xs: xs[0] if len(xs) == 1 else None
    if DECLINE.search(value):
        return one([o for o in options if DECLINE.search(o)])
    v = norm(value)
    for same in SAME.get(kind, []):
        # A saved "none" answers having a disability, not wanting adjustments or other conditions.
        if v in same and not (kind == "disability" and not re.search(r"\bdisabilit(y|ies)\b|\bdisabled\b", label, re.I)) and \
                not re.search(r"\btrans", label, re.I) and not re.search(r"adjustment|accommodation", label, re.I):
            return one([o for o in options if norm(o) in same])
    if kind == "ethnicity":
        # Most specific first: the whole answer, then its parts ("Asian - Chinese" also tries "Chinese").
        parts = [v, *[norm(x) for x in reversed(re.split(r"[-/,(;:]| or ", value))]]
        for x in (x for x in parts if len(x) > 3):
            if (m := one([o for o in options if re.search(rf"\b{re.escape(x)}\b", norm(o))])):
                return m
    return None


def is_letter(f: dict) -> bool:
    return f["kind"] == "textarea" and bool(re.match(r"(your )?cover(ing)? (letter|note)", norm(f["label"])))


def letter_text(body: str, name: str) -> str:
    """Greeting and name are added here, so the name is never sent to the model."""
    body = body.strip()
    if not body.lower().startswith("dear"):
        return f"Dear Hiring Team,\n\n{body}\n\nKind regards,\n{name}"
    return f"{body}\n{name}" if re.search(r"(yours \w+|regards|sincerely|wishes),?$", body, re.I) else body


def plan(fs: list[dict], profile: dict, cv: dict | None = None, tailored: dict | None = None) -> tuple[dict[str, dict], list[dict]]:
    """(answers known without the model, fields to ask the model about)."""
    known, ask = {}, []
    code = next((c for c in DIAL if re.sub(r"[^+\d]", "", profile.get("phone") or "").startswith(c)), None)
    if code and any(re.fullmatch(r"(country )?(phone|dial\w*) code|country phone code", norm(f["label"])) for f in fs):
        profile = {**profile, "phone": re.sub(r"[^+\d]", "", profile["phone"])[len(code):]}      # the code has its own box
        profile["_dial"] = code
    for f in fs:
        base = {"label": f["label"], "kind": f["kind"], "required": bool(f["required"]), "options": f["opts"][:80], "policy": f["policy"]}
        p = f["policy"]
        if tailored and not p.startswith("sensitive:") and p != "file" and (a := from_cv(f, tailored, cv, profile)) is not None:
            if f["kind"] == "checkbox" or not a["value"] and not a["note"]:
                f["policy"], f["required"] = "ok", f["required"] and bool(a["value"])
            known[f["key"]] = {**base, "policy": f["policy"], **a}
            continue
        if p.startswith("sensitive:"):
            v = (profile.get("sensitive") or {}).get(p.split(":")[1])
            m = (match_option(v, f["opts"]) if f["opts"] else v) if v else None
            near = None if m or not v else match_personal(p.split(":")[1], v, f["label"], f["opts"])
            if near:
                known[f["key"]] = {**base, "value": [near], "basis": "contact", "check": True,
                                   "note": "Closest option to your saved personal answer. Check it."}
                continue
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
