"""Code-level check that tailored text adds nothing. Runs before the AI reviewer and cannot be argued with."""
import re

from .parse import all_text, flat, header

WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
TECH = set("""python java javascript typescript c++ c# rust golang kotlin swift scala ruby php perl matlab haskell ocaml sql nosql
react angular vue svelte next.js node node.js express django flask fastapi spring rails laravel .net graphql rest grpc html css
tailwind bootstrap docker kubernetes terraform ansible jenkins aws azure gcp lambda s3 ec2 linux bash git github gitlab ci/cd
kafka rabbitmq redis postgres postgresql mysql sqlite mongodb dynamodb cassandra elasticsearch snowflake bigquery spark hadoop
airflow dbt pandas numpy scipy scikit-learn sklearn tensorflow pytorch keras jax opencv huggingface llm llms rag nlp cuda
tableau excel jira confluence asana trello figma agile scrum kanban prince2 waterfall selenium cypress junit pytest playwright
android ios flutter unity unreal blockchain solidity machine-learning deep-learning microservices fpga verilog""".split())
# Verbs that claim ownership. Allowed only if the cited facts use the same stem.
ESCALATE = {"led": "led|lead", "managed": "manag", "architected": "architect", "owned": "own", "founded": "found",
            "spearheaded": "spearhead", "directed": "direct", "headed": "head", "mentored": "mentor",
            "launched": "launch", "pioneered": "pioneer", "supervised": "supervis", "oversaw": "oversaw|oversee"}
IGNORE_CAPS = {"I", "A", "The", "UK", "API", "APIs"}


def toks(s: str) -> set[str]:
    return {t.strip(".,;:()[]'\"!?").lower() for t in re.split(r"[\s/–—]+", s)} | \
           {t.strip(".,;:()[]'\"!?").lower() for t in re.split(r"\s+", s)}


def numbers(s: str) -> set[str]:
    s = s.lower()
    out = {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s)}
    return out | {WORDS[w] for w in re.findall(r"[a-z]+", s) if w in WORDS and w != "one"}


def _stem(w: str) -> str:
    return re.sub(r"('s|s)$", "", w.lower())


def unsupported(text: str, source: str) -> list[str]:
    """Terms in `text` with no basis in `source`."""
    bad = []
    for n in sorted(numbers(text) - numbers(source)):
        bad.append(f"number {n}")
    src = toks(source)
    for t in sorted((toks(text) & TECH) - src):
        bad.append(f"technology '{t}'")
    src_stems = {_stem(t) for t in src} | {_stem(t) for t in re.findall(r"[A-Za-z0-9+#]+", source)}
    for sent in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z][A-Za-z0-9+#.&'-]*", sent)
        for i, w in enumerate(words):
            w = w.strip(".'-")
            if not w or w in IGNORE_CAPS or w.lower() in TECH:
                continue
            named = (w[0].isupper() and i > 0) or re.search(r"[a-z][A-Z]|[A-Z]{2,}", w)
            if named and not all(_stem(p) in src_stems for p in re.split(r"[-&.']", w) if len(p) > 1):
                bad.append(f"name '{w}'")
    low = source.lower()
    for verb, stem in ESCALATE.items():
        if re.search(rf"\b{verb}\b", text.lower()) and not re.search(rf"\b({stem})", low):
            bad.append(f"ownership verb '{verb}'")
    return list(dict.fromkeys(bad))


def check_cv(t: dict, cv: dict) -> list[dict]:
    issues, facts = [], flat(cv)
    entries = {e["id"]: e for e in cv["entries"]}
    skills = {i for s in cv["skills"] for i in s["items"]}

    def block(where, problem):
        issues.append({"severity": "block", "where": where, "problem": problem,
                       "suggestion": "Reword using only the cited facts.", "by": "code"})

    seen = set()
    for e in t["entries"]:
        src = entries.get(e["entry_id"])
        if not src:
            block(e["entry_id"], "Entry does not exist in your CV.")
            continue
        if e["entry_id"] in seen:
            block(e["entry_id"], "Entry appears twice.")
        seen.add(e["entry_id"])
        for b in e["bullets"]:
            where = f"{src['organisation'] or src['title']}: {b['text'][:60]}"
            bad = [f for f in b["fact_ids"] if f not in facts or not f.startswith(e["entry_id"] + ".")]
            if bad or not b["fact_ids"]:
                block(where, f"Cites {'no facts' if not b['fact_ids'] else 'facts outside this entry: ' + ', '.join(bad)}.")
                continue
            miss = unsupported(b["text"], header(src) + " " + " ".join(facts[f] for f in b["fact_ids"]))
            if miss:
                block(where, "Not in the cited facts: " + ", ".join(miss))
    for s in t["skills"]:
        for i in s["items"]:
            if i not in skills:
                block("Skills", f"'{i}' is not in your CV.")
    for e in cv["entries"]:
        if e["kind"] == "education" and e["id"] not in seen:
            block("Education", f"{e['organisation']} is missing.")
    return issues


def check_prose(where: str, text: str, cv: dict, profile: dict, job: dict, research=(), notes: str = "") -> list[dict]:
    """Letters and answers may also draw on the posting and profile, and letters on company research and your own reasons."""
    own = all_text(cv) + " " + " ".join(str(v) for k, v in profile.items() if k != "sensitive") + " " + notes
    src = own + f" {job['company']} {job['title']} {' '.join(job['locations'] or [])} {job['description']} " + " ".join(f["quote"] for f in research)
    miss = unsupported(text, src)
    # Technology in the posting is not evidence that the candidate has used it; warn so a human looks.
    soft = [f"technology '{t}'" for t in sorted((toks(text) & TECH) - toks(own)) if f"technology '{t}'" not in miss]
    out = []
    if miss:
        out.append({"severity": "block", "where": where, "problem": "No source for: " + ", ".join(miss),
                    "suggestion": "Remove or replace with a fact from your CV.", "by": "code"})
    if soft:
        out.append({"severity": "warn", "where": where, "problem": "About the employer, not in your CV: " + ", ".join(soft),
                    "suggestion": "Check this does not imply experience you do not have.", "by": "code"})
    return out


# The app read those pages, not you, so a letter must not say you did unless you told it so.
CLAIMED = re.compile(r"\bI(?:'ve|’ve|'m|’m| have| am)?(?: been)?(?: long| always| recently| closely| also)* "
                     r"(read|reading|follow(?:ed|ing)?|admire[ds]?|attended|met with|spoke (?:to|with)|watched|listened to)\b"
                     r"|\bas an? (?:\w+[- ]){0,2}(user|customer|reader|follower|fan)\b", re.I)


def check_letter(text: str, cv: dict, profile: dict, job: dict, research=(), notes: str = "") -> list[dict]:
    out = check_prose("Cover letter", text, cv, profile, job, research, notes)
    said = [m[0] for m in CLAIMED.finditer(text) if not re.search(rf"\b{re.escape((m[1] or m[2])[:5])}", notes, re.I)]
    if said:
        out.append({"severity": "block", "where": "Cover letter", "problem": f"Says something about you that you have not told the app: \"{said[0]}\".",
                    "suggestion": "Remove it, or if it is true, add it to your own reasons and write the letter again.", "by": "code"})
    own = all_text(cv) + " " + " ".join(str(v) for k, v in profile.items() if k != "sensitive") + " " + notes
    about_me = " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(r"\b(I|my)\b", s))
    odd = sorted(numbers(about_me) - numbers(own))
    if odd and not any(i["severity"] == "block" for i in out):
        out.append({"severity": "warn", "where": "Cover letter", "problem": "A sentence about you has a figure that is only in the employer's text: " + ", ".join(odd) + ".",
                    "suggestion": "Check it does not read as your own result.", "by": "code"})
    return out
