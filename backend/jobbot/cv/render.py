"""Tailored CV to a one-page A4 PDF using Chromium."""
import copy
import html
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from .. import config

h = lambda s: html.escape(s or "")
SECTIONS = [("education", "Education"), ("experience", "Experience"), ("project", "Projects"),
            ("activity", "Activities"), ("award", "Awards")]
CSS = (Path(__file__).parent / "templates" / "cv.css").read_text()


def to_html(t: dict, cv: dict, profile: dict, size: float = 10.4) -> str:
    entries = {e["id"]: e for e in cv["entries"]}
    by = {k: [] for k, _ in SECTIONS}
    for te in t["entries"]:
        if te["entry_id"] in entries:
            by[entries[te["entry_id"]]["kind"]].append((entries[te["entry_id"]], te["bullets"]))
    name = f"{profile['first_name']} {profile['last_name']}".strip() or cv["name"]
    contact = [profile["email"] or cv["email"], profile["phone"] or cv["phone"], profile["city"],
               profile["linkedin"], profile["github"], profile["website"]]
    contact = [c.removeprefix("https://").removeprefix("www.") for c in contact if c]
    out = [f"<h1>{h(name)}</h1><p class=c>{' &nbsp;·&nbsp; '.join(map(h, contact))}</p>"]
    for kind, title in SECTIONS:
        if not by[kind]:
            continue
        out.append(f"<h2>{title}</h2>")
        for e, bullets in by[kind]:
            sub = " · ".join(filter(None, [h(e["organisation"]), h(e["location"])]))
            out.append(f"<div class=e><div class=r><b>{h(e['title'])}</b><span>{h(e['dates'])}</span></div>"
                       + (f"<div class=o>{sub}</div>" if sub else "")
                       + ("<ul>" + "".join(f"<li>{h(b['text'])}</li>" for b in bullets) + "</ul>" if bullets else "") + "</div>")
    if any(s["items"] for s in t["skills"]):
        out.append("<h2>Skills</h2>" + "".join(
            f"<p class=s><b>{h(s['group'])}:</b> {h(', '.join(s['items']))}</p>" for s in t["skills"] if s["items"]))
    return f"<!doctype html><meta charset=utf-8><title>{h(name)} CV</title><style>:root{{--s:{size}pt}}{CSS}</style>{''.join(out)}"


def render_letter(text: str, cv: dict, profile: dict, job: dict) -> Path:
    """The approved letter as a PDF, made when a form asks for a cover letter file."""
    folder = config.DATA / "applications" / str(job["id"])
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{profile['first_name']} {profile['last_name']}".strip() or cv["name"]
    path = folder / f"{name.replace(' ', '_').replace('/', '-')}_Cover_Letter.pdf"
    paras = "".join(f"<p style='margin:0 0 .8em'>{h(p)}</p>" for p in text.split("\n\n"))
    body = (f"<h1>{h(name)}</h1><p class=c>{h(profile['email'] or cv['email'])}</p><p style='margin:1.2em 0 .8em'>"
            f"{h(job['title'])}, {h(job['company'])}</p><p style='margin:0 0 .8em'>Dear Hiring Team,</p>{paras}<p>Kind regards,<br>{h(name)}</p>")
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            page = b.new_page(java_script_enabled=False)
            page.set_content(f"<!doctype html><meta charset=utf-8><style>:root{{--s:11pt}}{CSS}</style>{body}")
            page.pdf(path=str(path), format="A4", prefer_css_page_size=True)
        finally:
            b.close()
    return path


def fit(t: dict, cv: dict, profile: dict, job_id: int) -> tuple[dict, Path, int, list[str]]:
    """Renders, dropping the weakest bullets if they spill over the page. Bullets run strongest first, so the last of the
    longest entry goes. Returns (cv, path, pages, dropped)."""
    t, dropped = copy.deepcopy(t), []
    path, pages = render(t, cv, profile, job_id)
    while pages > 1 and len(dropped) < 6:
        e = max(reversed(t["entries"]), key=lambda e: len(e["bullets"]), default=None)
        if not e or len(e["bullets"]) < 2:
            break
        dropped.append(e["bullets"].pop()["text"])
        path, pages = render(t, cv, profile, job_id)
    return t, path, pages, dropped


def render(t: dict, cv: dict, profile: dict, job_id: int) -> tuple[Path, int]:
    """Returns (path, pages). Shrinks slightly to fit one page; never below 9pt."""
    folder = config.DATA / "applications" / str(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    last = (profile["last_name"] or cv["name"].split()[-1] if cv["name"] else "CV").replace("/", "-")
    path = folder / f"{(profile['first_name'] or 'CV').replace('/', '-')}_{last}_CV.pdf"
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            page = b.new_page(java_script_enabled=False)
            for size in (10.4, 10.0, 9.6, 9.2, 9.0):
                page.set_content(to_html(t, cv, profile, size))
                page.pdf(path=str(path), format="A4", print_background=True, prefer_css_page_size=True)
                pages = len(PdfReader(str(path)).pages)
                if pages == 1:
                    break
        finally:
            b.close()
    return path, pages
