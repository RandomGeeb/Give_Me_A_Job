"""Read forms and put approved answers into them. Never submits, never presses Enter."""
import re
from pathlib import Path

from playwright.sync_api import Frame, Page, Route
from playwright.sync_api import Error as PWError

from .fields import JS, add_keys, match_option, norm

GUARD = """() => { if (!window.__jbGuard) { window.__jbGuard = true;
  document.addEventListener('submit', e => { if (window.__jbFilling) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
  document.addEventListener('keydown', e => { if (window.__jbFilling && e.key === 'Enter') { e.preventDefault(); e.stopImmediatePropagation(); } }, true); } }"""
PAINT = """(e, [c, t]) => { const b = e.closest('.ashby-application-form-field-entry, .application-question, .field-wrapper, .select, fieldset') || e;
  b.style.outline = '3px solid ' + c; b.style.outlineOffset = '3px'; b.title = t; }"""
GREEN, AMBER, RED = "#1a9850", "#f0a202", "#d73027"
SKIP_FRAMES = re.compile(r"recaptcha|hcaptcha|turnstile|doubleclick|google\.com|youtube")


def read_only(route: Route) -> None:
    """Block writes. GraphQL reads are POSTs, so allow those unless they contain a mutation."""
    r = route.request
    if r.method in ("GET", "HEAD", "OPTIONS"):
        return route.continue_()
    body = r.post_data or ""
    if "graphql" in r.url and "mutation" not in body.lower() and '"query' in body:
        return route.continue_()
    route.abort()


def frames(page: Page) -> list[Frame]:
    return [f for f in page.frames if f == page.main_frame or (f.url.startswith("http") and not SKIP_FRAMES.search(f.url))]


def wait_ready(page: Page, ms: int = 9000) -> None:
    try:
        page.wait_for_selector("input:not([type=hidden]), textarea, select", timeout=ms, state="attached")
    except PWError:
        pass
    page.wait_for_timeout(1500)


def extract(page: Page, with_options: bool = True) -> list[dict]:
    out = []
    for i, fr in enumerate(frames(page)):
        try:
            raw = fr.evaluate(JS)
        except PWError:
            continue
        for f in raw:
            f["frame"] = i
            if with_options and f["kind"] == "combobox":
                f["options"] = [{"label": o} for o in read_combobox(fr, f["jb"])]
        out += raw
    return add_keys(out)


def _listbox(fr: Frame, loc):
    """The option list belonging to one combobox, so options of another are never clicked."""
    lid = loc.get_attribute("aria-controls") or loc.get_attribute("aria-owns")
    scope = fr.locator(f'[id="{lid}"]') if lid else fr.locator('[role="listbox"]:visible')
    return scope.locator('[role="option"]')


def read_combobox(fr: Frame, jb: str) -> list[str]:
    loc = fr.locator(f'[data-jb="{jb}"]')
    try:
        loc.click(timeout=2000)
        fr.wait_for_timeout(150)
        opts = [re.sub(r"\s+", " ", t).strip() for t in _listbox(fr, loc).all_inner_texts()]
        loc.press("Escape")
        opts = [o for o in opts if o and not re.match(r"no options|loading|start typing", o, re.I)]
        return opts if len(opts) <= 80 else []       # long lists are searched when filling
    except PWError:
        return []


def _pick(fr: Frame, loc, value: str) -> bool:
    loc.click(timeout=3000)
    fr.wait_for_timeout(200)
    for typed in (False, True):
        if typed:
            loc.fill("")
            loc.press_sequentially(value[:60], delay=15)
            fr.wait_for_timeout(1300)
        opts = _listbox(fr, loc)
        texts = [re.sub(r"\s+", " ", t).strip() for t in opts.all_inner_texts()]
        m = match_option(value, texts)
        if m is not None:
            opts.nth(texts.index(m)).click(timeout=3000)
            return True
    loc.press("Escape")
    return False


def _file_for(f: dict, files: dict[str, Path | None]) -> Path | None:
    s = norm(f["label"] + " " + f.get("hint", ""))
    if "transcript" in s:
        return files.get("transcript")
    if "cover" in s:
        return files.get("cover_letter")
    if re.search(r"resume|\bcv\b|curriculum", s):
        return files.get("cv")
    return None


def fill(page: Page, fs: list[dict], answers: dict[str, dict], files: dict[str, Path | None]) -> dict:
    """Returns {filled, needs_you: [labels], failed: [labels]}."""
    res = {"filled": 0, "needs_you": [], "failed": []}
    frs = frames(page)
    for fr in frs:
        try:
            fr.evaluate(GUARD)
            fr.evaluate("() => { window.__jbFilling = true }")
        except PWError:
            pass
    try:
        for f in fs:
            fr = frs[f["frame"]] if f["frame"] < len(frs) else frs[0]
            a = answers.get(f["key"])
            anchor = fr.locator(f'[data-jb="{f.get("jb") or f["options"][0]["jb"]}"]').first
            try:
                if f["kind"] == "file":
                    path = _file_for(f, files)
                    if path:
                        anchor.set_input_files(str(path))
                        colour, note = GREEN, f"Attached {path.name}"
                        res["filled"] += 1
                    elif f["required"]:
                        colour, note = RED, "Needs a file from you"
                        res["needs_you"].append(f["label"])
                    else:
                        continue
                elif f["policy"] == "consent" or not a or not a["value"]:
                    if not f["required"] and f["policy"] == "ok":
                        continue
                    colour, note = RED, (a or {}).get("note") or "Needs you"
                    res["needs_you"].append(f["label"])
                elif put(fr, f, a["value"]):
                    colour = GREEN if a["basis"] == "contact" or a.get("edited") else AMBER
                    note = a.get("note") or a["basis"]
                    res["filled"] += 1
                else:
                    colour, note = RED, f"Could not enter: {', '.join(a['value'])[:120]}"
                    res["failed"].append(f["label"])
            except PWError:
                colour, note = RED, "Could not fill this field"
                res["failed"].append(f["label"])
            try:
                anchor.evaluate(PAINT, [colour, note])
            except PWError:
                pass
    finally:
        for fr in frs:
            try:
                fr.evaluate("() => { window.__jbFilling = false }")
            except PWError:
                pass
    return res


def put(fr: Frame, f: dict, value: list[str]) -> bool:
    k = f["kind"]
    if k in ("radio", "checkbox"):
        by = {o["label"]: o["jb"] for o in f["options"]}
        if any(v not in by for v in value):
            return False
        for v in value:
            loc = fr.locator(f'[data-jb="{by[v]}"]')
            try:
                loc.check(force=True, timeout=2000)
            except PWError:              # covered by a pop-up, or a styled control
                if not loc.is_checked():
                    loc.evaluate("e => e.click()")
            if not loc.is_checked():
                return False
        return True
    if k == "yesno":
        o = next((o for o in f["options"] if o["label"].lower() == value[0].lower()), None)
        if not o:
            return False
        fr.locator(f'[data-jb="{o["jb"]}"]').click(timeout=3000)
        return True
    loc = fr.locator(f'[data-jb="{f["jb"]}"]')
    if k == "select":
        m = match_option(value[0], f["opts"])
        if not m:
            return False
        loc.select_option(label=m, timeout=3000)
        return True
    if k == "combobox":
        return _pick(fr, loc, value[0])
    text = value[0][:f["maxlength"]] if f.get("maxlength") else value[0]
    if k == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return False
    loc.fill(text.replace("\n", " ") if k == "text" else text, timeout=3000)
    return loc.input_value() != ""
