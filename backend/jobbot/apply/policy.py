"""Rules about which fields may be answered, by whom. Code, not prompts."""
import re

SENSITIVE = {
    "gender": r"\bgender\b|\bsex\b(?! offen)|transgender|\btrans\b",
    "pronouns": r"\bpronouns?\b",
    "ethnicity": r"\bethnic\w*|\brace\b|racial|hispanic|latin[ox]\b",
    "orientation": r"sexual orientation|\blgbt",
    "religion": r"religio|\bfaith\b|\bbelief\b",
    "disability": r"disab|neurodiver|reasonable adjustment|accommodation|long[- ]term (health|condition)|health condition|medical",
    "veteran": r"veteran|military service|armed forces",
    "age": r"date of birth|\bdob\b|birth ?date|\bage (group|range|band)|your age\b|how old",
    "marital": r"marital|pregnan|caring responsib|\bcarer\b",
    "criminal": r"criminal|convict|unspent|offence|\barrest",
    "identity": r"passport|national insurance|\bnino\b|national id|\bnric\b|social security|\bssn\b|driving licen[cs]e|visa number",
    "socioeconomic": r"free school meals|socio.?economic|parent(s|al)?('s)? (occupation|qualification|job|education|highest)|type of school|school (type|did you attend).*(state|private)|household earner|first in your family",
    "bank": r"bank account|sort code|credit card",
}
SENSITIVE = {k: re.compile(v, re.I) for k, v in SENSITIVE.items()}
CONSENT = re.compile(
    r"\bi (agree|consent|certify|confirm|acknowledge|accept|declare|understand|authori[sz]e|have read)\b|privacy (policy|notice|statement)|"
    r"terms (and|&) conditions|terms of use|data protection|\bgdpr\b|by (ticking|checking|clicking|submitting|selecting)|"
    r"declaration|true and (accurate|complete)|retain my|talent (pool|community)|marketing|future opportunities|sms|text messages?|"
    r"\bconsent\b", re.I)
# Site furniture that happens to be a form: never part of an application.
NOT_APPLICATION = re.compile(r"newsletter|subscri(be|ption)|insights and updates|job alerts?|talent alerts?|search (jobs|roles|site|openings)|"
                             r"^search$|cookie|(choose|select) (your )?(language|region)|^(language|region)$", re.I)
AI_RULE = re.compile(r"without (the )?(use|using|help|assistance)( of)? (an? )?(ai|llm|chatgpt)|(not|don't|do not|never) use (an? )?(ai|chatgpt|llm|generative)|"
                     r"\bai[- ](generated|assist)|\b(have|did) you use[d]? (any )?(ai|chatgpt|generative)", re.I)


def classify(label: str, kind: str, options: list[str] | None = None) -> str:
    """'sensitive:<name>' | 'consent' | 'file' | 'ai_rule' | 'ok'. Earlier rules win."""
    if kind == "file":
        return "file"
    for name, rx in SENSITIVE.items():
        if rx.search(label):
            return f"sensitive:{name}"
    opts = " ".join(options or [])
    if re.search(r"prefer not to (say|answer|disclose)|decline to (self|answer|state)|don't wish to answer|do not wish to (answer|disclose)", opts, re.I):
        return "sensitive:other"
    if CONSENT.search(label) and (kind in ("checkbox", "yesno", "radio", "select", "combobox")):
        return "consent"
    if kind == "checkbox" and len(options or []) == 1:
        return "consent"       # a lone tick-box is nearly always a declaration
    if AI_RULE.search(label):
        return "ai_rule"
    return "ok"
