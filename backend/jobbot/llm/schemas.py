from typing import Literal

from pydantic import BaseModel


# ---- CV parsing ----
class ParsedEntry(BaseModel):
    kind: Literal["education", "experience", "project", "activity", "award"]
    title: str
    organisation: str
    location: str
    dates: str
    bullets: list[str]


class ParsedSkillGroup(BaseModel):
    group: str
    items: list[str]


class ParsedCV(BaseModel):
    name: str
    email: str
    phone: str
    links: list[str]
    entries: list[ParsedEntry]
    skills: list[ParsedSkillGroup]


# ---- scoring ----
class Requirement(BaseModel):
    requirement: str
    essential: bool
    met: Literal["yes", "partial", "no", "unknown"]
    evidence: str


class Score(BaseModel):
    is_relevant_internship: bool
    family: Literal["software", "data_ml_quant", "project", "product", "analyst", "other"]
    eligible: Literal["yes", "no", "unsure"]
    eligibility_notes: list[str]
    dates: Literal["fit", "slight", "conflict", "unknown"]
    requirements: list[Requirement]
    competition: Literal["normal", "high", "extreme"]
    deadline: str | None
    application_limit: str | None
    summary: str


class Rating(BaseModel):
    id: str
    is_relevant_internship: bool
    eligible: Literal["yes", "no", "unsure"]
    dates: Literal["fit", "slight", "conflict", "unknown"]
    must_met: int
    must_partial: int
    must_missing: int
    must_unknown: int
    nice_met: int
    nice_missing: int
    competition: Literal["normal", "high", "extreme"]
    deadline: str | None
    reason: str


class Ratings(BaseModel):
    ratings: list[Rating]


# ---- tailoring ----
class TailoredBullet(BaseModel):
    text: str
    fact_ids: list[str]


class TailoredEntry(BaseModel):
    entry_id: str
    bullets: list[TailoredBullet]


class TailoredSkills(BaseModel):
    group: str
    items: list[str]


class TailoredCV(BaseModel):
    entries: list[TailoredEntry]
    skills: list[TailoredSkills]
    changes: list[str]


class LetterParagraph(BaseModel):
    text: str
    fact_ids: list[str]
    finding_ids: list[str]


class CoverLetter(BaseModel):
    paragraphs: list[LetterParagraph]


class Answer(BaseModel):
    field: str
    value: list[str]
    basis: Literal["profile", "cv", "drafted", "unknown"]
    note: str


class Answers(BaseModel):
    answers: list[Answer]


# ---- review ----
class Issue(BaseModel):
    severity: Literal["block", "warn", "note"]
    where: str
    problem: str
    suggestion: str


class Review(BaseModel):
    employer_restricts_ai: bool
    ai_rule_quote: str
    issues: list[Issue]
    strengths: list[str]


# ---- company research ----
class Finding(BaseModel):
    page: str
    quote: str
    topic: str
    published: str | None


class CompanyFindings(BaseModel):
    findings: list[Finding]


# ---- web search ----
class FoundRole(BaseModel):
    page: str
    link: str | None
    title: str
    company: str
    locations: list[str]
    country: Literal["uk", "singapore", "other"]
    still_open: Literal["yes", "no", "unknown"]
    deadline: str | None


class FoundRoles(BaseModel):
    roles: list[FoundRole]
