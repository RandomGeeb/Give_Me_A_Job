"""Deterministic filters. They decide only what is clearly out; borderline roles go to scoring."""
import re

from .models import RawJob

UK = re.compile(
    r"\b(uk|u\.k\.|gb|gbr|united kingdom|great britain|england|scotland|wales|northern ireland|london|manchester|"
    r"edinburgh|glasgow|bristol|leeds|belfast|birmingham|cardiff|newcastle|sheffield|nottingham|"
    r"liverpool|oxford|cambridge|reading|brighton|southampton|coventry|york|bath|dundee|aberdeen|"
    r"milton keynes|guildford|peterborough|swindon|leicester|warwick|bournemouth|stevenage)\b", re.I)
# Same place names elsewhere. Applied per location string, so "London, UK; Cambridge, MA" still passes.
UK_EXPLICIT = re.compile(r"\b(uk|u\.k\.|united kingdom|great britain|england|scotland|wales|northern ireland|gb|gbr)\b", re.I)
NOT_UK = re.compile(
    r"\b(ON|MA|NH|OH|KY|CT|AL|PA|NY|NJ|CA|TX|MD|ME|NSW|ontario|canada|usa|united states|massachusetts|"
    r"new york|new jersey|new south wales|new england|australia|connecticut|alabama|pennsylvania|"
    r"new hampshire|ohio|kentucky|ireland(?<!northern ireland))\b")
NOT_UK_I = re.compile(NOT_UK.pattern.replace("ON|MA|NH|OH|KY|CT|AL|PA|NY|NJ|CA|TX|MD|ME|NSW|", ""), re.I)

INTERN = re.compile(
    r"\bintern(ship)?s?\b|\bplacements?\b|industrial year|industrial attachment|year in industry|sandwich (year|course)|"
    r"\bsummer (analyst|associate|scheme|programme|program)\b|spring (week|insight)|off[- ]cycle", re.I)
NOT_INTERN = re.compile(r"\binternal\b.*\bmanager\b|placements? (manager|officer|coordinator|consultant)|"
                        r"(intern(ship)?s?|early careers?) (recruiter|recruitment|manager)|\bhead of\b", re.I)

FAMILIES = [
    ("product", r"\bproduct (manag\w*|owner|operations|analyst)|\bAPM\b|\bproduct intern\b"),
    ("project", r"\b(project|program|programme|delivery|portfolio) (manag\w*|coordinat\w*|support|office\w*|analyst|delivery)|"
                r"\bPMO\b|scrum master|^(?!.*\bdata\b).*\bproject intern\b"),
    ("data_ml_quant", r"machine learning|\bML\b|\bAI\b|artificial intelligence|\bdata\b|deep learning|\bNLP\b|"
                      r"computer vision|quant\w*|algorithmic|research(er)? (engineer|scientist)|trad(er|ing)"),
    ("software", r"software|developer|\bSWE\b|\bSDE\b|engineer(ing)?.{0,20}(platform|backend|back-end|frontend|front-end|"
                 r"full[- ]?stack|mobile|ios|android|cloud|security|infrastructure|devops)|"
                 r"(platform|backend|back-end|frontend|front-end|full[- ]?stack|mobile|ios|android|cloud|web|security|"
                 r"cyber\w*|infrastructure|devops|site reliability|technology) (engineer\w*|develop\w*|intern\w*|security)|"
                 r"\bSRE\b|programmer|computer science|forward deployed|cyber"),
    ("analyst", r"\b(business|technology|technical|systems?|IT|digital|process) (analyst|analysis|consult\w*)|"
                r"tech(nology)? consult\w*|technology (summer )?(analyst|intern\w*|placement|programme|program)|"
                r"solutions? (engineer|architect|analyst)|\bIT (intern\w*|placement)"),
]
FAMILIES = [(n, re.compile(p, re.I)) for n, p in FAMILIES]
OFF_TOPIC = re.compile(
    r"marketing|human resources|\bHR\b|people team|\btalent\b|recruit|\blegal\b|\blaw\b|paralegal|accountan|\baudit\b|"
    r"\btax\b|\bsales\b|social media|content creator|graphic design|fashion|civil engineer|mechanical engineer|"
    r"chemical engineer|structural|architectural|nurs(e|ing)|clinical|teaching|\bhardware\b|\bFPGA\b|\bASIC\b|"
    r"analog|\bRF\b|silicon|electrical engineer|electronics|aerospace|manufacturing|supply chain|real estate|"
    r"interior|biolog|biotech|electrician|apprentice|chemist|communications intern", re.I)


def is_uk(locations: list[str]) -> bool:
    for loc in locations:
        for part in re.split(r"[;|/•]|\bor\b", loc):
            if UK_EXPLICIT.search(part) and not re.search(r"\bnew (england|south wales)\b", part, re.I):
                return True
            if UK.search(part) and not NOT_UK.search(part) and not NOT_UK_I.search(part):
                return True
    return False


SG = re.compile(r"\bsingapore\b", re.I)
SG_CODE = re.compile(r"\b(SG|SGP)\b")
REGIONS = {"uk": "United Kingdom", "sg": "Singapore"}
OUTSIDE = "outside your countries"


def regions(locations: list[str]) -> list[str]:
    out = ["uk"] if is_uk(locations) else []
    if any(SG.search(x) or SG_CODE.search(x) for x in locations):
        out.append("sg")
    return out


NO_COMPANY = re.compile(r"^$|not (shown|stated|disclosed|specified)|^(unknown|confidential|anonymous|n/?a)$", re.I)


def family(title: str) -> str | None:
    for name, rx in FAMILIES:
        if rx.search(title):
            return name
    return None


def classify(j: RawJob, families: list[str], wanted: list[str] = ("uk", "sg")) -> tuple[str | None, str | None]:
    """Returns (family, reason_if_filtered_out)."""
    if not set(regions(j.locations)) & set(wanted):
        return None, OUTSIDE
    if not INTERN.search(j.title) or NOT_INTERN.search(j.title):
        return None, "not an internship or placement"
    fam = family(j.title)
    if NO_COMPANY.search(j.company.strip()):
        return fam, "company not shown"
    if OFF_TOPIC.search(j.title) and fam not in ("software", "project", "product"):
        return fam, "off-topic role"
    if fam is None:
        # "Summer Intern 2027", "Engineering Intern": ambiguous, so let scoring read the description.
        if re.search(r"\bengineer|\btechnolog|\btechnical|\btech\b(?! college)|digital|\bIT\b|comput|cloud|platform|security|systems|informatics|\bGPU\b|formal methods|^research intern|applied scientist", j.title, re.I):
            return "software", None
        return None, "no matching role family"
    if fam not in families:
        return fam, f"{fam} roles are switched off"
    return fam, None
