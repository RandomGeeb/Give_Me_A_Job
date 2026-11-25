"""Read forms and put approved answers into them. Never submits. Enter is only ever pressed to search in a Workday list."""
import re
from pathlib import Path

from playwright.sync_api import Frame, Page, Route
from playwright.sync_api import Error as PWError

from . import policy
from .fields import JS, add_keys, match_option, norm

GUARD = """() => { if (!window.__jbGuard) { window.__jbGuard = true;
  document.addEventListener('submit', e => { if (window.__jbFilling) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
  document.addEventListener('keydown', e => { if (window.__jbFilling && e.key === 'Enter' && !e.target.closest('[data-automation-id="multiSelectContainer"]')) { e.preventDefault(); e.stopImmediatePropagation(); } }, true); } }"""
PAINT = """(e, [c, t]) => { const g = t.startsWith('Best guess'); const b = e.closest('.ashby-application-form-field-entry, .application-question, .field-wrapper, .select, .text-input-wrapper, [data-automation-id^="formField"], fieldset') || e;
  b.style.outline = '3px ' + (g ? 'dashed ' : 'solid ') + c; b.style.outlineOffset = '3px'; b.title = t;
  if (!b.__jbWatch) { b.__jbWatch = true; const yours = x => { if (window.__jbFilling || x.target.type === 'file') return;
    b.style.outline = '3px solid #1a9850'; b.title = 'Kept what you entered'; };
    b.addEventListener('input', yours); b.addEventListener('change', yours); } }"""
ARIA = """e => e.closest('[role=radio], [role=checkbox]')?.getAttribute('aria-checked') === 'true'"""
# What a field holds now, to tell when you have changed it since it was last filled.
STATE = """(e, ids) => { const all = ids.map(i => document.querySelector('[data-jb="' + i + '"]')).filter(Boolean);
  if (e.type === 'radio' || e.type === 'checkbox') return all.map(x =>
    +(x.checked || x.closest('[role=radio], [role=checkbox]')?.getAttribute('aria-checked') === 'true')).join('');
  if (e.matches('button[aria-haspopup]')) return /^(select( one)?|choose.*|)$/i.test(e.innerText.trim()) ? '' : e.innerText.trim();
  const multi = e.closest('[data-automation-id="multiSelectContainer"], [data-uxi-widget-type="multiselect"]');
  if (multi) return [...multi.querySelectorAll('[data-automation-id="selectedItem"]')].map(x => x.innerText.trim()).join('|');
  const dated = e.closest('[data-automation-id="dateInputWrapper"]');
  if (dated) return [...dated.querySelectorAll('input')].map(x => x.value).join('/').replace(/^[/]+$/, '');
  if (e.tagName === 'BUTTON') return all.map(x => x.className + x.ariaPressed + x.ariaChecked + (x.parentElement.dataset.v || '')).join('|');
  const box = e.getAttribute('role') === 'combobox' && e.closest('[class*="control"]');
  return (e.value + ' ' + (box ? box.innerText : '')).replace(/\\s+/g, ' ').trim(); }"""
GREEN, YELLOW, RED, GREY = "#1a9850", "#f2c500", "#d73027", "#8a8f98"
STEP = (Path(__file__).parent / "step.js").read_text()
WHERE = """() => document.querySelector('[data-automation-id="progressBarActiveStep"]')?.innerText || ''"""
# Presses the one button step.js marked, and only if it still says what it said then.
PRESS = """() => { const b = document.querySelector('[data-jbnext]');
  const t = b && (b.innerText || b.value || '').replace(/[→›»>]/g, '').replace(/\\s+/g, ' ').trim();
  if (!b || b.disabled || !/^(save (and|&) (continue|next|proceed)|next( step| page| section)?|continue)$/i.test(t)) return false;
  b.click(); return true; }"""
POPUP = """() => { let n = 0; document.querySelectorAll('[data-jbo]').forEach(e => e.removeAttribute('data-jbo'));
  return [...document.querySelectorAll('[data-automation-id="promptOption"]')].filter(e => e.getClientRects().length && !e.closest('[data-automation-id="selectedItem"]'))
    .map(e => { e.setAttribute('data-jbo', n++); return { text: (e.dataset.automationLabel || e.innerText || '').replace(/\\s+/g, ' ').trim(),
      size: +(e.closest('[aria-setsize]')?.getAttribute('aria-setsize') || 0) }; }); }"""
# Opens as many empty work and education blocks as the CV needs. A block the site already filled in is left alone.
PANELS = """([kind, want]) => { const rx = kind === 'work' ? /^(work-|professional-|employment-)?(experience|history|employment)/i : /^education/i;
  const gh = document.querySelector(kind === 'work' ? '.employment--container' : '.education--container');          // Greenhouse
  const sec = gh || [...document.querySelectorAll('[role=group][aria-labelledby$="-section"]')].find(s => rx.test(s.getAttribute('aria-labelledby')));
  if (!sec) return null;
  const panels = [...sec.querySelectorAll(gh ? '.employment-form, .education--form' : '[role=group][aria-labelledby$="-panel"]')];
  const blank = p => ![...p.querySelectorAll('input, textarea')].some(i => i.type !== 'checkbox' && i.value.trim()) && !p.querySelector('.select__single-value');
  panels.forEach(p => { if (blank(p)) p.setAttribute('data-jb-ours', '1'); });
  const ours = panels.every(p => p.hasAttribute('data-jb-ours'));
  const add = gh ? [...sec.querySelectorAll('button.add-another-button[type=button]')].find(b => /^add another$/i.test(b.innerText.trim())) :
    [...sec.querySelectorAll('[data-automation-id="add-button"]')].filter(b => !b.closest('[aria-labelledby$="-panel"]')).pop();
  if (panels.length >= want) sec.setAttribute('data-jb-done', '1');          // so a block you remove stays removed
  const more = ours && panels.length < want && !sec.hasAttribute('data-jb-done') && !!add && !add.disabled;
  if (more) add.click();
  return { n: panels.length, more }; }"""
PREFILL = {"text", "textarea", "number", "date", "radio", "checkbox", "dropdown", "multiselect", "dateparts"}
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
            if with_options and not f.get("theirs"):
                if f["kind"] in ("combobox", "dropdown"):
                    f["options"] = [{"label": o} for o in read_combobox(fr, f["jb"])]
                elif f["kind"] == "multiselect":
                    f["options"] = [{"label": o} for o in read_prompt(fr, f["jb"])]
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
        opts = [o for o in opts if o and not re.match(r"no options|loading|start typing|select one$", o, re.I)]
        return opts if len(opts) <= 80 else []       # long lists are searched when filling
    except PWError:
        return []


def read_prompt(fr: Frame, jb: str) -> list[str]:
    """What a Workday list shows before searching, if it shows everything. Longer lists are searched when filling."""
    loc = fr.locator(f'[data-jb="{jb}"]')
    try:
        loc.click(timeout=2000)
        fr.wait_for_timeout(900)
        opts = fr.evaluate(POPUP)
        loc.press("Escape")
        fr.wait_for_timeout(150)
    except PWError:
        return []
    return [] if any(o["size"] > len(opts) for o in opts) or len(opts) > 60 else [o["text"] for o in opts if o["text"]]


def _pills(loc) -> list[str]:
    return [re.sub(r"\s+", " ", x).strip() for x in (loc.evaluate(STATE, [], timeout=2000) or "").split("|") if x.strip()]


def _prompt(fr: Frame, loc, value: str, label: str) -> bool:
    """Choose one thing from a Workday list: as shown, then as you type, then by searching."""
    has = lambda: match_option(value, _pills(loc)) is not None
    if has():
        return True
    before = _pills(loc)
    grown = lambda: _pills(loc) != before
    loc.click(timeout=3000)
    for how in ("shown", "typed", "searched"):
        if how == "typed":
            loc.fill("", timeout=2000)
            loc.press_sequentially(value[:60], delay=15)
        elif how == "searched":
            loc.press("Enter")
        fr.wait_for_timeout(900 if how == "shown" else 1400)
        if how == "searched":                  # the search can take a few seconds to come back
            for _ in range(6):
                if grown() or (o := fr.evaluate(POPUP)) and any(norm(value.split()[0]) in norm(x["text"]) for x in o):
                    break
                fr.wait_for_timeout(600)
        if how != "shown" and (has() or grown() and len(_pills(loc)) == 1 and set(norm(value).split()) <= set(norm(_pills(loc)[0]).split())):          # a single match can be chosen for you
            return True
        opts = fr.evaluate(POPUP)
        texts = [o["text"] for o in opts]
        m = match_option(value, texts)
        if m is None and how == "searched" and re.search(r"field of study|major|subject|discipline", label, re.I):
            # Subject lists word things their own way: "Computer Science - General", "Computer and Information Science".
            words = [w for w in norm(value).split() if w not in ("and", "of", "the")]
            near = [t for t in texts if all(w in norm(t).split() for w in words)]
            m = min(near, key=len) if near else None
        if m is None:
            continue
        fr.locator(f'[data-jbo="{[o["text"] for o in opts].index(m)}"]').click(timeout=3000)
        fr.wait_for_timeout(900)
        if grown():
            return True
        inner = [o["text"] for o in fr.evaluate(POPUP)]        # it was a heading with more underneath
        pick = match_option(value, inner) or (next((o for o in inner if re.search(r"web ?site|careers|job board|online|internet|other", o, re.I)), None)
                                              if re.search(r"hear|source", label, re.I) else None)
        if pick:
            fr.locator(f'[data-jbo="{inner.index(pick)}"]').click(timeout=3000)
            fr.wait_for_timeout(700)
            return grown()
        break
    loc.fill("", timeout=2000)
    loc.press("Escape")
    return False


def _date(fr: Frame, f: dict, value: str) -> bool:
    m = re.match(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value)
    got = dict(zip(("year", "month", "day"), m.groups())) if m else {}
    if any(not got.get(p) for p in f["parts"]):
        return False
    for part, jb in zip(f["parts"], f["tags"]):
        loc, want = fr.locator(f'[data-jb="{jb}"]'), int(got[part])
        loc.focus(timeout=2000)
        loc.press_sequentially(f"{want:04d}" if part == "year" else f"{want:02d}", delay=20)
        fr.wait_for_timeout(120)
        if not (loc.input_value() or "x").isdigit() or int(loc.input_value()) != want:
            loc.fill(str(want), timeout=2000)
        if not loc.input_value().isdigit() or int(loc.input_value()) != want:
            return False
    return True


def step(page: Page) -> dict:
    """Where the application has got to, from the frame that holds it."""
    best = None
    for fr in frames(page):
        try:
            s = fr.evaluate(STEP)
        except PWError:
            continue
        if best is None or s["signin"] and not best["signin"] or (s["next"] or s["submit"]) and not (best["next"] or best["submit"]):
            best = {**s, "url": best["url"] if best else s["url"]}
            best["frame"] = fr
    if best is None:
        raise PWError("The page is still loading.")
    return best


def press_next(page: Page) -> bool:
    """Move to the next page. Never the last one: step.js marks a button only when more steps are known to follow."""
    s = step(page)
    return bool(s["safe"]) and bool(s["frame"].evaluate(PRESS))


def begin(page: Page, s: dict) -> None:
    """From an advert to the form itself, without ever using a saved or CV-read application."""
    if s["start"]:
        page.locator(f'[data-automation-id="{"applyManually" if s["start"] == "manual" else "adventureButton"}"]').first.evaluate("e => e.click()")
    else:
        page.goto(s["apply"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2500)


def add_panels(page: Page, work: int) -> None:
    for kind, want in (("work", work), ("education", 1)):
        for _ in range(want + 1):
            more = False
            for fr in frames(page):
                try:
                    r = fr.evaluate(PANELS, [kind, want])
                except PWError:
                    continue
                more = more or bool(r and r["more"])
            if not more:
                break
            page.wait_for_timeout(700)


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


def _retag(page: Page, fs: list[dict]) -> None:
    """The page rebuilt its form (often after a CV is attached), so find the same fields again."""
    new = {f["key"]: f for f in extract(page, with_options=False)}
    for f in fs:
        n = new.get(f["key"])
        if n:
            f["frame"] = n["frame"]
            if "jb" in n:
                f["jb"] = n["jb"]
            if "tags" in n:
                f["tags"] = n["tags"]
            if any("jb" in o for o in n["options"]):
                f["options"] = n["options"]


def _state(fr: Frame, f: dict, anchor) -> str | None:
    try:
        return anchor.evaluate(STATE, [o["jb"] for o in f["options"] if "jb" in o] or [f.get("jb")], timeout=2000)
    except PWError:
        return None


def fill(page: Page, fs: list[dict], answers: dict[str, dict], files: dict[str, Path | None], only: set | None = None, waiting: set = frozenset()) -> dict:
    """only: keys to fill, all if None. waiting: keys being drafted, left alone for now. Anything you changed since the last
    fill is kept. Returns {filled, kept, check: [{label, note, guess}], needs_you: [labels], failed: [labels], blocking: [labels]}."""
    res = {"filled": 0, "kept": 0, "check": [], "needs_you": [], "failed": [], "blocking": []}
    frs = frames(page)
    where = page.evaluate(WHERE)          # some sites show every step in one page, with the same question on two steps
    page.set_default_timeout(4000)
    for fr in frs:
        try:
            fr.evaluate(GUARD)
            fr.evaluate("() => { window.__jbFilling = true }")
        except PWError:
            pass
    # Grey until reached, so a page part-way through does not look as if fields were missed.
    for f in fs if only is None else []:
        a = answers.get(f["key"])
        if f["kind"] != "file" and (f["key"] in waiting or f["policy"] != "consent" and a and a["value"]):
            try:
                frs[min(f["frame"], len(frs) - 1)].locator(f'[data-jb="{f.get("jb") or f["options"][0]["jb"]}"]').first.evaluate(PAINT, [GREY, "Waiting to be filled"], timeout=500)
            except PWError:
                pass
    try:
        for f in fs:
            a = answers.get(f["key"])
            if only is not None and f["key"] not in only:
                continue
            find = lambda: frs[min(f["frame"], len(frs) - 1)].locator(f'[data-jb="{f.get("jb") or f["options"][0]["jb"]}"]').first
            try:
                if not find().count():
                    page.wait_for_timeout(1500)
                    frs = frames(page)
                    _retag(page, fs)
            except PWError:
                pass
            fr, anchor = frs[min(f["frame"], len(frs) - 1)], find()
            now = last = None
            if f["kind"] != "file":
                now = _state(fr, f, anchor)
                try:
                    last = fr.evaluate("k => (window.__jbSeen || {})[k] ?? null", where + f["key"])
                except PWError:
                    pass
            try:
                if f["key"] in waiting:
                    colour, note = GREY, "Being written now"
                elif last is not None and now is not None and now != last and now.strip("0"):
                    colour, note = GREEN, "Kept what you entered"
                    res["kept"] += 1
                elif f["kind"] == "file":
                    path = _file_for(f, files)
                    there = f.get("there") or []
                    if path and any(x["name"] == path.name and x["size"] in (None, path.stat().st_size) for x in there):
                        colour, note = GREEN, f"{path.name} was already attached"
                    elif path and any(x["size"] is None for x in there):
                        colour, note = RED, f"Already has {there[0]['name']}: check it, or replace it with {path.name}"
                        res["needs_you"].append(f"{f['label']}: already has {there[0]['name']}")
                    elif path:
                        anchor.set_input_files(str(path), timeout=15000)
                        page.wait_for_timeout(800)        # some sites rebuild the form once a file is attached
                        colour, note = GREEN, f"Attached {path.name}"
                        res["filled"] += 1
                    elif f["required"]:
                        colour, note = RED, "Needs a file from you"
                        res["needs_you"].append(f["label"])
                        res["blocking"].append(f["label"])
                    else:
                        continue
                elif last is None and now and now.strip("0") and f["kind"] in PREFILL and (f["policy"] == "consent" or not a or not a["value"] or a["basis"] == "guess"):
                    colour, note = YELLOW, "Already filled in by the site"
                    res["check"].append({"label": f["label"], "note": note + ".", "guess": False})
                elif f["policy"] == "consent" or not a or not a["value"]:
                    if not f["required"] and (f["policy"] == "ok" or re.search(r"preferred name", f["label"], re.I)):
                        continue
                    if policy.must_stop(f):
                        res["blocking"].append(f["label"])
                    colour, note = RED, (a or {}).get("note") or "Needs you"
                    res["needs_you"].append(f["label"])
                elif put(fr, f, a["value"]):
                    guess = a["basis"] == "guess"
                    yours = not a.get("check") and (a["basis"] == "contact" or a.get("edited"))
                    colour = GREEN if yours else YELLOW
                    note = ("Best guess: " if guess else "") + (a.get("note") or a["basis"])
                    res["filled"] += 1
                    if not yours:
                        res["check"].append({"label": f["label"], "note": a.get("note") or "", "guess": guess})
                else:
                    colour, note = RED, f"Could not enter: {', '.join(a['value'])[:120]}"
                    res["failed"].append(f["label"])
                    res["blocking"] += [f["label"]] * bool(f["required"])
            except PWError:
                colour, note = RED, "Could not fill this field"
                res["failed"].append(f["label"])
                res["blocking"] += [f["label"]] * bool(f["required"])
            try:
                anchor.evaluate(PAINT, [colour, note], timeout=2000)
                if f["kind"] != "file" and (seen := _state(fr, f, anchor)) is not None and note != "Kept what you entered":
                    fr.evaluate("([k, v]) => { (window.__jbSeen ||= {})[k] = v }", [where + f["key"], seen])
            except PWError:
                pass
    finally:
        page.set_default_timeout(30000)
        for fr in frs:
            try:
                fr.evaluate("() => { window.__jbFilling = false }")
            except PWError:
                pass
    return res


def _ticked(fr: Frame, loc) -> bool:
    """Some forms show a choice a moment after the click, or only on the element around the real control."""
    for _ in range(8):
        if loc.is_checked() or loc.evaluate(ARIA):
            return True
        fr.wait_for_timeout(100)
    return False


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
                if not _ticked(fr, loc):
                    loc.evaluate("e => e.click()")
            if not _ticked(fr, loc):
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
    if k == "dateparts":
        return _date(fr, f, value[0])
    if k == "multiselect":
        if len(value) == 1:
            return _prompt(fr, loc, value[0], f["label"])
        return any([_prompt(fr, loc, v, f["label"]) for v in value])      # skills the site does not list are left out
    if k == "dropdown":
        if norm(loc.inner_text()) == norm(value[0]):
            return True
        loc.click(timeout=3000)
        fr.wait_for_timeout(300)
        opts = _listbox(fr, loc)
        texts = [re.sub(r"\s+", " ", t).strip() for t in opts.all_inner_texts()]
        m = match_option(value[0], texts)
        if m is None:
            loc.press("Escape")
            return False
        opts.nth(texts.index(m)).click(timeout=3000)
        fr.wait_for_timeout(500)
        return True
    text = value[0][:f["maxlength"]] if f.get("maxlength") else value[0]
    if k == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return False
    loc.fill(text.replace("\n", " ") if k == "text" else text, timeout=3000)
    return loc.input_value() != ""
