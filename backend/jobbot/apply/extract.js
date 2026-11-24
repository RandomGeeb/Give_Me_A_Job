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
  const boxOf = e => e.closest(BOX);
  const titleOf = e => { let b = boxOf(e);
    for (let k = 0; b && k < 3; k++, b = b.parentElement?.closest(BOX)) {
      const t = b.querySelector(TITLE); if (t && clean(t.innerText)) return clean(t.innerText); }
    return ''; };
  const starred = (e, label) => e.required || e.getAttribute('aria-required') === 'true' ||
    /[*✱]/.test((boxOf(e)?.querySelector(TITLE)?.innerText) || '') || !!boxOf(e)?.querySelector('[class*="_required"],.required');
  document.querySelectorAll('[data-jb]').forEach(e => e.removeAttribute('data-jb'));
  let n = 0; const tag = e => { const id = 'jb' + (n++); e.setAttribute('data-jb', id); return id; };
  const out = [], groups = new Map();
  for (const e of document.querySelectorAll('input, textarea, select')) {
    const type = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'password', 'search'].includes(type)) continue;
    if (/captcha/i.test((e.name || '') + (e.id || ''))) continue;
    if (e.disabled || e.readOnly) continue;
    if (e.closest('.ashby-application-form-autofill-input-root, .iti__dropdown-content, [class*="autofill-input"]')) continue;
    const entry = e.closest('.ashby-application-form-field-entry');
    if (type === 'checkbox' && entry && entry.querySelector('[class*="_yesno"]')) {
      const btns = [...entry.querySelectorAll('[class*="_yesno"] button, button')].filter(b => /^(yes|no)$/i.test(b.innerText.trim()));
      if (btns.length) { out.push({ kind: 'yesno', label: titleOf(e), required: starred(e),
        options: btns.map(b => ({ label: b.innerText.trim(), jb: tag(b) })) }); continue; }
    }
    if (type === 'radio' || type === 'checkbox') {
      const label = own(e) || clean(e.value);
      const lab = (e.id || '').match(/^(.*)-labeled-(checkbox|radio)-\d+$/);
      const gid = lab?.[1] || entry?.getAttribute('data-field-entry-id') || (e.name ? type + ':' + e.name : null) || boxOf(e) || e;
      if (!shown(e) && !(e.labels?.[0] && shown(e.labels[0]))) continue;
      if (!groups.has(gid)) { const g = { kind: type, label: '', required: starred(e), options: [], first: e }; groups.set(gid, g); out.push(g); }
      groups.get(gid).options.push({ label, jb: tag(e) });
      continue;
    }
    if (type === 'file') { out.push({ kind: 'file', label: titleOf(e) || own(e) || e.name || e.id, jb: tag(e), required: starred(e), options: [],
        hint: (e.id + ' ' + e.name) }); continue; }
    if (!shown(e)) continue;
    if (e.tabIndex < 0 && !e.getAttribute('role')) continue;          // react-select shadow inputs
    if (!e.id && !e.name && !own(e) && e.getAttribute('role') !== 'combobox') continue;
    let label = own(e); if (!label || label.length < 2) label = titleOf(e) || clean(e.placeholder) || e.name || e.id;
    if (e.tagName === 'SELECT') {
      out.push({ kind: 'select', label, jb: tag(e), required: starred(e),
        options: [...e.options].filter(o => o.value && clean(o.text)).map(o => ({ label: clean(o.text) })) }); continue; }
    const kind = e.getAttribute('role') === 'combobox' ? 'combobox' : e.tagName === 'TEXTAREA' ? 'textarea' : type === 'number' ? 'number' : type === 'date' ? 'date' : 'text';
    out.push({ kind, label, jb: tag(e), required: starred(e), options: [], maxlength: e.maxLength > 0 ? e.maxLength : null, hint: e.id + ' ' + e.name });
  }
  for (const g of groups.values()) {
    const t = titleOf(g.first), lone = g.options.length === 1;
    g.label = lone ? [t, g.options[0].label].filter((x, i, a) => x && a.indexOf(x) === i).join(' - ') : (t || g.first.name);
    if (!lone && g.options.some(o => o.label === g.label)) {     // title was really the first option's label
      const fs = g.first.closest('fieldset, [role=group], [role=radiogroup]');
      const by = fs?.getAttribute('aria-labelledby');
      g.label = clean((by && document.getElementById(by)?.innerText) || fs?.querySelector('legend')?.innerText || g.first.name);
    }
    delete g.first;
  }
  return out;
}
