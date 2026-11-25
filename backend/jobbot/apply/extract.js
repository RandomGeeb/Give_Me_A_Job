// Tags every fillable field with data-jb and returns a description of each. Reads only.
() => {
  const BOX = '.ashby-application-form-field-entry, .application-question, fieldset, .field-wrapper, .select, ' +
    '[data-automation-id^="formField"], .form-group, .form-field, .field, .oj-form-layout';
  const TITLE = '.ashby-application-form-question-title, .application-label, legend, .label, label';
  const clean = s => (s || '').replace(/[✱*]+\s*$/g, '').replace(/\s+/g, ' ').replace(/\s*[✱*]+\s*/g, ' ').trim();
  const shown = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && r.right > 0; };
  const own = e => {
    if (e.labels && e.labels.length) {
      const l = e.labels[0], t = l.querySelector('.application-label');
      return clean(t ? t.innerText : l.innerText);
    }
    const by = e.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' '); if (clean(t)) return clean(t); }
    return clean(e.getAttribute('aria-label'));
  };
  const named = e => clean(document.getElementById((e.getAttribute('aria-labelledby') || '').split(/\s+/)[0])?.innerText);
  const boxOf = e => e.closest(BOX);
  const titleOf = e => { let b = boxOf(e);
    for (let k = 0; b && k < 3; k++, b = b.parentElement?.closest(BOX)) {
      const t = b.querySelector(TITLE); if (t && clean(t.innerText)) return clean(t.innerText); }
    return ''; };
  // Repeated blocks, such as Workday's "Work Experience 2". Panels already filled in by the site are left alone.
  const panelOf = e => e.closest('[role=group][aria-labelledby$="-panel"]');
  const BLOCK = '.employment-form, .education--form';            // Greenhouse's version of the same thing
  const blockOf = e => { const b = e.closest(BLOCK); if (!b) return null;
    const all = [...b.parentElement.children].filter(x => x.matches(BLOCK));
    return { b, name: (b.matches('.employment-form') ? 'Employment ' : 'Education ') + (all.indexOf(b) + 1) }; };
  const untouched = b => ![...b.querySelectorAll('input, textarea')].some(i => i.type !== 'checkbox' && i.value.trim()) && !b.querySelector('.select__single-value');
  const place = (f, e) => { const k = blockOf(e);
    if (k) { f.section = k.name; f.theirs = !k.b.hasAttribute('data-jb-ours') && !untouched(k.b); f.name = f.label;
      if (k.name !== 'Education 1') f.label = k.name + ': ' + f.label; return f; }
    const p = panelOf(e); if (!p) return f;
    f.section = p.getAttribute('aria-labelledby').replace(/-panel$/, '').replace(/-/g, ' ');
    f.theirs = !p.hasAttribute('data-jb-ours'); f.name = f.label; f.label = f.section + ': ' + f.label; return f; };
  const partOf = e => (e.closest('[role=group][aria-labelledby$="-section"]')?.getAttribute('aria-labelledby') || '').replace(/-section$/, '').replace(/-/g, ' ');
  const starred = (e, label) => e.required || e.getAttribute('aria-required') === 'true' ||
    /[*✱]/.test((boxOf(e)?.querySelector(TITLE)?.innerText) || '') || !!boxOf(e)?.querySelector('[class*="_required"],.required');
  document.querySelectorAll('[data-jb]').forEach(e => e.removeAttribute('data-jb'));
  let n = 0; const tag = e => { const id = 'jb' + (n++); e.setAttribute('data-jb', id); return id; };
  const out = [], groups = new Map();
  for (const e of document.querySelectorAll('input, textarea, select, button[aria-haspopup="listbox"]')) {
    if (e.tagName === 'BUTTON') {            // a drop-down built from a button
      if (!shown(e) || e.disabled || !(e.closest('[data-automation-id^="formField"]') || e.labels?.length)) continue;
      const label = titleOf(e) || own(e);
      if (label) out.push(place({ kind: 'dropdown', label, jb: tag(e), required: starred(e) || /\brequired\b/i.test(e.getAttribute('aria-label') || ''), options: [] }, e));
      continue;
    }
    const type = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'password', 'search'].includes(type)) continue;
    if (e.closest('[data-automation-id="signInContent"]')) continue;         // signing in is yours to do
    if (/captcha|beecatcher/i.test((e.name || '') + (e.id || '') + (e.dataset.automationId || ''))) continue;
    if (e.previousElementSibling?.matches('button[aria-haspopup="listbox"]')) continue;      // holds that button's value
    const dated = e.closest('[data-automation-id="dateInputWrapper"]');
    if (dated) {
      const part = (e.dataset.automationId + ' ' + e.id + ' ' + (e.getAttribute('aria-label') || '')).match(/month|day|year/i)?.[0].toLowerCase();
      if (!part) continue;
      let g = groups.get(dated);
      if (!g) { g = place({ kind: 'dateparts', label: titleOf(e) || own(e), required: starred(e), options: [], parts: [] }, e); groups.set(dated, g); out.push(g); }
      g.parts.push(part); (g.tags ||= []).push(tag(e)); g.jb = g.tags[0];
      continue;
    }
    const multi = e.closest('[data-automation-id="multiSelectContainer"], [data-uxi-widget-type="multiselect"]');
    if (multi) {
      if (shown(multi)) out.push(place({ kind: 'multiselect', label: [titleOf(e), own(e), partOf(e), e.placeholder].find(x => x && !/^search$/i.test(x)) || 'Search',
        jb: tag(e), required: starred(e), options: [] }, e));
      continue;
    }
    if (e.disabled || e.readOnly) continue;
    if (e.closest('.ashby-application-form-autofill-input-root, .iti__dropdown-content, [class*="autofill-input"]')) continue;
    const entry = e.closest('.ashby-application-form-field-entry');
    if (type === 'checkbox' && entry && entry.querySelector('[class*="_yesno"]')) {
      const btns = [...entry.querySelectorAll('[class*="_yesno"] button, button')].filter(b => /^(yes|no)$/i.test(b.innerText.trim()));
      if (btns.length) { out.push({ kind: 'yesno', label: titleOf(e), required: starred(e),
        options: btns.map(b => ({ label: b.innerText.trim(), jb: tag(b) })) }); continue; }
    }
    if (type === 'radio' || type === 'checkbox') {
      const near = e.closest('[data-automation-id^="formField"]');
      const label = own(e) || (type === 'checkbox' && near ? clean(near.innerText) : '') || clean(e.value);
      const lab = (e.id || '').match(/^(.*)-labeled-(checkbox|radio)-\d+$/);
      const wd = e.closest('[data-automation-id^="formField"]');
      let gid = (type === 'checkbox' && wd?.querySelectorAll('input[type=checkbox]').length > 1 && wd) || lab?.[1] || entry?.getAttribute('data-field-entry-id') || (e.name ? type + ':' + e.name : null) || boxOf(e) || e;
      const blk = blockOf(e);
      if (blk && typeof gid === 'string') gid = blk.name + '|' + gid;
      if (!shown(e) && !(e.labels?.[0] && shown(e.labels[0]))) continue;
      if (!groups.has(gid)) { const g = { kind: type, label: '', required: starred(e), options: [], first: e }; groups.set(gid, g); out.push(g); }
      groups.get(gid).options.push({ label, jb: tag(e) });
      continue;
    }
    if (type === 'file') {
      const zone = e.closest('[role=group]') || e.parentElement;
      const there = e.files.length ? [...e.files].map(x => ({ name: x.name, size: x.size })) :
        [...zone.querySelectorAll('[data-automation-id="file-upload-item-name"]')].map(x => ({ name: clean(x.innerText), size: null }));
      out.push({ there, kind: 'file', label: titleOf(e) || named(e) || own(e) || e.name || e.id || partOf(e) || 'File', jb: tag(e), required: starred(e), options: [],
        hint: [e.id, e.name, e.dataset.ui, partOf(e)].join(' ') }); continue; }
    if (!shown(e)) continue;
    if (e.tabIndex < 0 && !e.getAttribute('role')) continue;          // react-select shadow inputs
    if (!e.id && !e.name && !own(e) && e.getAttribute('role') !== 'combobox') continue;
    let label = own(e); if (!label || label.length < 2) label = titleOf(e) || clean(e.placeholder) || e.name || e.id;
    if (e.tagName === 'SELECT') {
      out.push({ kind: 'select', label, jb: tag(e), required: starred(e),
        options: [...e.options].filter(o => o.value && clean(o.text)).map(o => ({ label: clean(o.text) })) }); continue; }
    const kind = e.getAttribute('role') === 'combobox' ? 'combobox' : e.tagName === 'TEXTAREA' ? 'textarea' : type === 'number' ? 'number' : type === 'date' ? 'date' : 'text';
    out.push({ kind, label, jb: tag(e), required: starred(e), options: [], maxlength: e.maxLength > 0 ? e.maxLength : null, hint: e.id + ' ' + e.name });
    place(out[out.length - 1], e);
  }
  for (const g of groups.values()) {
    if (g.kind === 'dateparts') continue;
    const t = titleOf(g.first), lone = g.options.length === 1;
    g.label = lone ? [t, g.options[0].label].filter((x, i, a) => x && a.indexOf(x) === i).join(' - ') : (t || g.first.name);
    if (!lone && g.options.some(o => o.label === g.label)) {     // title was really the first option's label
      const fs = g.first.closest('fieldset, [role=group], [role=radiogroup]');
      const by = fs?.getAttribute('aria-labelledby');
      g.label = clean((by && document.getElementById(by)?.innerText) || fs?.querySelector('legend')?.innerText || g.first.name);
    }
    place(g, g.first); delete g.first;
  }
  return out;
}
