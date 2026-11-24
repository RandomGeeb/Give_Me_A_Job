from enum import StrEnum

from pydantic import BaseModel


class Status(StrEnum):
    discovered = "discovered"
    filtered_out = "filtered_out"
    scored = "scored"
    shortlisted = "shortlisted"
    tailoring = "tailoring"
    needs_review = "needs_review"
    approved = "approved"
    filling = "filling"
    submitted = "submitted"
    assessment = "assessment"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"
    closed = "closed"


S = Status
APPLIED = {S.submitted, S.assessment, S.interview, S.offer, S.rejected, S.withdrawn}
# What each status may move to. `withdrawn`/`closed` are reachable from any open status.
TRANSITIONS: dict[Status, set[Status]] = {
    S.discovered: {S.scored, S.filtered_out, S.shortlisted},
    S.filtered_out: {S.discovered},
    S.scored: {S.shortlisted, S.scored, S.filtered_out},
    S.shortlisted: {S.tailoring, S.scored, S.submitted},
    S.tailoring: {S.needs_review, S.shortlisted},
    S.needs_review: {S.approved, S.tailoring, S.shortlisted},
    S.approved: {S.filling, S.needs_review, S.tailoring, S.submitted},
    S.filling: {S.submitted, S.approved, S.needs_review},
    S.submitted: {S.assessment, S.interview, S.offer, S.rejected},
    S.assessment: {S.interview, S.offer, S.rejected},
    S.interview: {S.interview, S.offer, S.rejected},
    S.offer: {S.rejected},
    S.rejected: {S.submitted},   # undo a misclick
    S.withdrawn: {S.discovered},
    S.closed: {S.discovered},
}
for _s, _to in TRANSITIONS.items():
    if _s not in (S.rejected, S.withdrawn, S.closed):
        _to |= {S.withdrawn, S.closed}


def can_move(a: str, b: str) -> bool:
    return Status(b) in TRANSITIONS[Status(a)]


class RawJob(BaseModel):
    """What a source yields before filtering."""
    key: str                 # stable dedupe key, e.g. "gh:7942281"
    source: str
    company: str
    title: str
    locations: list[str] = []
    url: str
    apply_url: str = ""
    ats: str = "other"
    description: str = ""
    posted_at: str | None = None    # ISO date
    deadline: str | None = None
