// Where a multi-page application has got to. Reads only.
() => {
  const shown = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const text = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').replace(/[→›»>]/g, '').replace(/\s+/g, ' ').trim();
  const all = s => [...document.querySelectorAll(s)].filter(shown);
  const NEXT = /^(save (and|&) (continue|next|proceed)|next( step| page| section)?|continue)$/i;
  const SUBMIT = /\b(submit|send|finish|complete)\b.{0,30}$|^apply( now)?$/i;
  const wd = document.querySelector('[data-automation-id="progressBar"]');
  const bars = wd ? [...wd.querySelectorAll('[data-automation-id^="progressBar"][data-automation-id$="Step"]')] : [];
  const here = bars.findIndex(b => b.dataset.automationId === 'progressBarActiveStep');
  let step = here + 1, of = bars.length, name = here >= 0 ? text(bars[here]).replace(/^.*?step \d+ of \d+\s*/i, '') : '';
  if (!of) {
    const m = document.body.innerText.match(/\bstep (\d+) (?:of|\/) (\d+)\b/i);
    if (m) { step = +m[1]; of = +m[2]; }
    else {
      const cur = document.querySelector('[aria-current="step"]'), list = cur?.closest('ol, ul, [role=list]');
      const items = list ? [...list.children] : [], i = items.findIndex(x => x.contains(cur));
      if (i >= 0 && items.length > 1) { step = i + 1; of = items.length; name = text(items[i]).slice(0, 60); }
    }
  }
  const review = !!document.querySelector('[data-automation-id="applyFlowReviewPage"]') || /^review( and submit)?$/i.test(name);
  const buttons = all('button, input[type=submit], input[type=button], [role=button]').filter(b => !b.disabled && b.getAttribute('aria-disabled') !== 'true');
  const submit = buttons.some(b => SUBMIT.test(text(b)) && !b.closest('header, nav'));
  const foot = all('[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]');
  const nexts = wd ? foot.filter(b => NEXT.test(text(b))) : buttons.filter(b => NEXT.test(text(b)));
  document.querySelectorAll('[data-jbnext]').forEach(e => e.removeAttribute('data-jbnext'));
  // Only when the page says more steps follow, nothing on it submits, and exactly one button moves on.
  const safe = nexts.length === 1 && of > 1 && step > 0 && step < of && !review && !submit;
  if (safe) nexts[0].setAttribute('data-jbnext', '1');
  const adventure = !wd && all('[data-automation-id="adventureButton"]').find(b => /^(apply|apply now|continue application)$/i.test(text(b)));
  const manual = !wd && all('[data-automation-id="applyManually"]')[0];
  const apply = [...new Set(all('a[href]').filter(a => /^apply( now| online| here| for (this )?(job|role|position))?$/i.test(text(a)) && /^https?:/.test(a.href)).map(a => a.href))];
  const errors = all('[data-automation-id="errorHeading"] li, [data-automation-id="inputAlert"], [data-automation-id="errorMessage"]').map(text).filter(Boolean);
  const host = /(^|\.)myworkday(jobs|site)\.com$/.test(location.hostname);
  // Workday's sign-in pages do not always show a password box, and draw their progress bar before anything else.
  const signin = all('input[type=password]').length > 0 || /sign ?in|log ?in|create account/i.test(name) ||
    !!document.querySelector('[data-automation-id="signInContent"], [data-automation-id="SignInWithEmailButton"], [data-automation-id="createAccountSubmitButton"], [data-automation-id="signInSubmitButton"]') ||
    (host && !wd && /\/(login|register|createAccount|forgotPassword|passwordreset|activate)\b/i.test(location.pathname));
  return { host, signin, url: location.href.split('#')[0], workday: !!wd, step, of, name, review, submit, next: nexts.length > 0, safe,
    already: !!document.querySelector('[data-automation-id="alreadyAppliedPage"]'),
    start: manual ? 'manual' : adventure ? 'adventure' : '', apply: apply.length === 1 ? apply[0] : '',
    errors: [...new Set(errors)].slice(0, 8), heading: text(document.querySelector('h1, h2') || document.createElement('i')).slice(0, 80) };
}
