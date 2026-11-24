import { useEffect, useState } from 'react'
import { api, CV, FAMILY, REGION, useApi } from '../api'
import { Err, Field, Flag, useAction, FIT } from '../components/ui'

type P = Record<string, any>
type S = { profile: P; model: string; daily_calls: number; plan_stop_percent: number; web_search_calls: number; max_per_company: number; reuse_percent: number; families: string[]; regions: string[]; priors: Record<string, { interview: number; offer: number }>; cv: CV | null; cv_confirmed: boolean; transcript: boolean }
const PERSONAL: [string, string, string?][] = [['first_name', 'First name'], ['last_name', 'Last name'], ['preferred_name', 'Preferred name'], ['email', 'Email'], ['phone', 'Phone'], ['city', 'City'], ['country', 'Country'], ['linkedin', 'LinkedIn'], ['github', 'GitHub'], ['website', 'Website']]
const STUDY: [string, string, string?][] = [['university', 'University'], ['degree', 'Degree'], ['discipline', 'Subject'], ['degree_level', 'Level', "For example Bachelor's, second year"], ['grade', 'Grade so far'], ['study_start', 'Started'], ['graduation', 'Graduating'], ['availability', 'Availability', 'Dates you can work']]
const SENS: Record<string, string> = { gender: 'Gender', pronouns: 'Pronouns', ethnicity: 'Ethnicity', orientation: 'Sexual orientation', religion: 'Religion', disability: 'Disability', veteran: 'Military service', age: 'Date of birth' }

const NEED: Record<string, [string, string]> = {
  first_name: ['First name', 'Every form asks for it.'], last_name: ['Last name', 'Every form asks for it.'], email: ['Email', 'Every form asks for it.'],
  phone: ['Phone', 'Nearly every form asks for it.'], city: ['City', 'Most forms ask where you are based.'], university: ['University', 'Needed for eligibility.'],
  degree: ['Degree', 'Needed for eligibility.'], degree_level: ['Level', 'Many roles only take certain years.'], grade: ['Grade so far', 'Many forms ask for it.'],
  graduation: ['Graduating', 'Many roles only take certain graduation years.'], availability: ['Availability', 'Roles have fixed dates.'],
  work_rights: ['UK status', 'Every UK form asks for it.'], work_rights_sg: ['Singapore status', 'Singapore forms ask for it.'] }
const COUNTRY_OF: Record<string, string> = { work_rights: 'uk', needs_sponsorship_uk: 'uk', work_rights_sg: 'sg', needs_sponsorship_sg: 'sg' }

/** What is missing or too vague, worked out from what is on screen. */
function flags(p: P, regions: string[]): Record<string, Flag> {
  const out: Record<string, Flag> = {}
  for (const [k, [, why]] of Object.entries(NEED)) if (!String(p[k] ?? '').trim()) out[k] = { level: 'missing', text: why }
  for (const r of ['uk', 'sg']) if ((p[`needs_sponsorship_${r}`] ?? null) === null) out[`needs_sponsorship_${r}`] = { level: 'missing', text: `${REGION[r]} forms ask for it.` }
  const vague = (k: string, bad: boolean, text: string) => { if (bad && !out[k]) out[k] = { level: 'vague', text } }
  vague('availability', !/20\d\d/.test(p.availability ?? ''), 'Add the year.')
  vague('degree_level', !/year|final|penultimate/i.test(p.degree_level ?? ''), 'Add which year you are in, for example "Bachelor\'s, second year of three".')
  vague('graduation', !/20\d\d/.test(p.graduation ?? ''), 'Add the year.')
  vague('preferred_name', (p.preferred_name ?? '').trim().split(/\s+/).length > 1, 'Forms ask for a preferred first name, so use one name or leave it blank.')
  for (const k of ['work_rights', 'work_rights_sg']) vague(k, /visa|pass\b/i.test(p[k] ?? '') && String(p[k]).trim().split(/\s+/).length < 6, 'Add the work limits and end date shown for your visa.')
  for (const [k, r] of Object.entries(COUNTRY_OF)) if (!regions.includes(r)) delete out[k]
  return out
}

export default function Profile() {
  const { data, error, reload } = useApi<S>('/settings')
  const [s, set] = useState<S | null>(null)
  const act = useAction(), [saved, setSaved] = useState(false)
  const [dirty, setDirty] = useState(false)
  // A reload after uploading or confirming the CV must not undo edits that are not saved yet.
  useEffect(() => { if (data) set(old => old && dirty ? { ...old, cv: data.cv, cv_confirmed: data.cv_confirmed, transcript: data.transcript } : structuredClone(data)) }, [data])
  if (!s) return <Err e={error} />
  const p = (k: string, v: any) => { setSaved(false); setDirty(true); set({ ...s, profile: { ...s.profile, [k]: v } }) }
  const save = async () => { if (await act.run('s', () => api('/settings', 'PUT', { profile: s.profile, daily_calls: s.daily_calls, plan_stop_percent: s.plan_stop_percent, web_search_calls: s.web_search_calls, max_per_company: s.max_per_company, reuse_percent: s.reuse_percent, families: s.families, regions: s.regions, priors: s.priors }))) { setSaved(true); setDirty(false); reload() } }
  const edit = (x: S) => { setSaved(false); setDirty(true); set(x) }
  const fl = flags(s.profile, s.regions)
  const todo = [...(!s.cv ? [{ k: 'cv', label: 'Upload your CV', level: 'missing' }] : !s.cv_confirmed ? [{ k: 'cv', label: 'Confirm your CV facts', level: 'missing' }] : []),
    ...Object.entries(fl).map(([k, f]) => ({ k, label: NEED[k]?.[0] ?? ({ needs_sponsorship_uk: 'UK sponsorship', needs_sponsorship_sg: 'Singapore sponsorship', preferred_name: 'Preferred name' } as Record<string, string>)[k], level: f.level }))]
    .sort((a, b) => a.level.localeCompare(b.level))
  const go = (k: string) => { const e = document.getElementById(`f-${k}`); e?.scrollIntoView({ block: 'center' }); e?.querySelector<HTMLElement>('input, select, textarea')?.focus({ preventScroll: true }) }
  const text = ([k, l, h]: [string, string, string?]) => <Field key={k} id={`f-${k}`} label={l} hint={h} flag={fl[k]}><input value={s.profile[k] ?? ''} onChange={e => p(k, e.target.value)} /></Field>
  const file = (path: string) => (e: React.ChangeEvent<HTMLInputElement>) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) act.run('u', async () => { const b = new FormData(); b.append('file', f); await api(path, 'POST', b); await reload() }) }
  return <><header className="page"><div><h2>Profile</h2><p className="muted">Stored on this Mac only. CV facts and study details go to Claude through your Claude Code login; contact details and personal answers do not.</p></div>
    <div className="acts">{dirty && <span className="due">Unsaved changes</span>}{saved && <span role="status">Saved</span>}<button className="primary" disabled={!!act.busy} onClick={save}>Save profile</button></div></header>
    <Err e={act.error} close={act.clear} />
    <section className="panel todo" aria-label="Still to do">{todo.length === 0 ? <p>Everything needed is filled in.</p> : <><h3>Still to do ({todo.length})</h3><ul>{todo.map(t => <li key={t.k}><span className={`pill t-${t.level}`}>{t.level === 'missing' ? 'Needed' : 'Check'}</span><button className="plain" onClick={() => go(t.k)}>{t.label}</button></li>)}</ul></>}
      {!s.transcript && <p className="small muted">Optional: some employers also ask for a transcript.</p>}</section>
    <section className="panel" id="f-cv"><h3>CV</h3><div className="acts"><label className="btn">{act.busy === 'u' ? 'Reading…' : s.cv ? 'Replace CV (PDF)' : 'Upload CV (PDF)'}<input hidden type="file" accept="application/pdf" disabled={!!act.busy} onChange={file('/cv/upload')} /></label>
      <label className="btn">{s.transcript ? 'Replace transcript' : 'Upload transcript'}<input hidden type="file" accept="application/pdf" onChange={file('/transcript')} /></label></div>
      {s.cv && <CvEditor key={JSON.stringify(s.cv).length} cv={s.cv} confirmed={s.cv_confirmed} reload={reload} />}</section>
    <section className="panel"><h3>Contact</h3><div className="grid2">{PERSONAL.map(text)}</div></section>
    <section className="panel"><h3>Study</h3><div className="grid2">{STUDY.map(text)}</div></section>
    <section className="panel"><h3>Right to work</h3><p className="muted">Forms are answered only from the country the job is in.</p>
      {([['uk', 'work_rights', 'For example British citizen, settled status, or Student visa with its limits and end date.'], ['sg', 'work_rights_sg', 'For example Singapore citizen, permanent resident, or the pass you hold.']] as const).filter(([r]) => s.regions.includes(r)).map(([r, k, hint]) => <div className="grid2" key={r}>
        <Field id={`f-${k}`} flag={fl[k]} label={`${REGION[r]}: your exact status`} hint={hint}><input value={s.profile[k] ?? ''} onChange={e => p(k, e.target.value)} /></Field>
        <Field id={`f-needs_sponsorship_${r}`} flag={fl[`needs_sponsorship_${r}`]} label={`${REGION[r]}: need sponsorship, now or later?`}><select value={(s.profile[`needs_sponsorship_${r}`] ?? null) === null ? '' : String(s.profile[`needs_sponsorship_${r}`])} onChange={e => p(`needs_sponsorship_${r}`, e.target.value === '' ? null : e.target.value === 'true')}><option value="">Not answered</option><option value="false">No</option><option value="true">Yes</option></select></Field></div>)}</section>
    <section className="panel"><h3>Reusable answers</h3><p className="muted">Anything you are asked often: skill ratings, notice period, how you heard about roles.</p>
      {(s.profile.extra_answers as { question: string; answer: string }[]).map((q, i) => <div key={i} className="qa"><input aria-label="Question" placeholder="Question" value={q.question} onChange={e => p('extra_answers', s.profile.extra_answers.map((x: any, n: number) => n === i ? { ...x, question: e.target.value } : x))} /><input aria-label="Answer" placeholder="Answer" value={q.answer} onChange={e => p('extra_answers', s.profile.extra_answers.map((x: any, n: number) => n === i ? { ...x, answer: e.target.value } : x))} /><button className="plain" onClick={() => p('extra_answers', s.profile.extra_answers.filter((_: any, n: number) => n !== i))}>Remove</button></div>)}
      <button onClick={() => p('extra_answers', [...s.profile.extra_answers, { question: '', answer: '' }])}>Add answer</button></section>
    <section className="panel"><h3>Personal answers (optional)</h3><p className="muted">Used only to fill matching form questions. Never sent to Claude. Leave blank to answer each one yourself.</p><div className="grid2">{Object.entries(SENS).map(([k, l]) => <Field key={k} label={l}><input value={s.profile.sensitive?.[k] ?? ''} onChange={e => p('sensitive', { ...s.profile.sensitive, [k]: e.target.value })} /></Field>)}</div></section>
    <section className="panel"><h3>Limits</h3><div className="grid2"><Field label="Daily limit (Claude calls)" hint="The app's own limit, on top of your plan's. 0 turns it off."><input type="number" min={0} max={2000} value={s.daily_calls} onChange={e => edit({ ...s, daily_calls: +e.target.value })} /></Field>
      <Field label="Stop at this much of your Claude plan (%)" hint="Keeps the rest of your 5-hour and weekly limits for you."><input type="number" min={5} max={100} value={s.plan_stop_percent} onChange={e => edit({ ...s, plan_stop_percent: +e.target.value })} /></Field>
      <Field label="Claude calls per web search"><input type="number" min={2} max={100} value={s.web_search_calls} onChange={e => edit({ ...s, web_search_calls: +e.target.value })} /></Field>
      <Field label="Reuse a CV that matches by (%)" hint="Reuses a CV you already have when it shows this much of what the advert asks for. 0 always writes a new one."><input type="number" min={0} max={100} value={s.reuse_percent} onChange={e => edit({ ...s, reuse_percent: +e.target.value })} /></Field>
      <Field label="Applications per employer" hint="Some employers limit these."><input type="number" min={1} max={20} value={s.max_per_company} onChange={e => edit({ ...s, max_per_company: +e.target.value })} /></Field></div>
      <fieldset className="checks"><legend>Countries</legend>{Object.entries(REGION).map(([k, v]) => <label key={k}><input type="checkbox" checked={s.regions.includes(k)} onChange={e => edit({ ...s, regions: e.target.checked ? [...s.regions, k] : s.regions.filter(x => x !== k) })} />{v}</label>)}</fieldset>
      <fieldset className="checks"><legend>Role types</legend>{Object.entries(FAMILY).map(([k, v]) => <label key={k}><input type="checkbox" checked={s.families.includes(k)} onChange={e => edit({ ...s, families: e.target.checked ? [...s.families, k] : s.families.filter(x => x !== k) })} />{v}</label>)}</fieldset></section>
    <section className="panel"><h3>Starting assumptions</h3><p className="muted">Guesses, not measurements. Your own outcomes gradually replace them.</p><div className="scroll"><table><thead><tr><th>Rating</th><th>First interview (%)</th><th>Offer (%)</th></tr></thead><tbody>{Object.entries(s.priors).map(([b, v]) => <tr key={b}><td>{FIT[b] ?? b}</td>{(['interview', 'offer'] as const).map(k => <td key={k}><input type="number" step="0.1" aria-label={`${FIT[b] ?? b} ${k}`} value={+(v[k] * 100).toFixed(2)} onChange={e => edit({ ...s, priors: { ...s.priors, [b]: { ...v, [k]: +e.target.value / 100 } } })} /></td>)}</tr>)}</tbody></table></div></section></>
}

function CvEditor({ cv, confirmed, reload }: { cv: CV; confirmed: boolean; reload: () => void }) {
  const [d, set] = useState<CV>(() => structuredClone(cv)); const act = useAction(); const [open, setOpen] = useState(!confirmed)
  const edit = (fn: (x: CV) => void) => { const c = structuredClone(d); fn(c); set(c) }
  return <div><p>{confirmed ? 'Confirmed. Every tailored CV is built only from these facts.' : 'Check each line against your CV. Nothing is tailored until you confirm.'} <button className="plain" onClick={() => setOpen(!open)}>{open ? 'Hide' : 'Show'} facts</button></p><Err e={act.error} />
    {open && <>{d.entries.map((e, i) => <div key={i} className="entry"><div className="grid2">{(['title', 'organisation', 'location', 'dates'] as const).map(k => <Field key={k} label={k[0].toUpperCase() + k.slice(1)}><input value={e[k]} onChange={v => edit(x => { x.entries[i][k] = v.target.value })} /></Field>)}<Field label="Section"><select value={e.kind} onChange={v => edit(x => { x.entries[i].kind = v.target.value })}>{['education', 'experience', 'project', 'activity', 'award'].map(k => <option key={k}>{k}</option>)}</select></Field></div>
      {e.bullets.map((b, n) => <div key={n} className="now"><textarea aria-label="Fact" rows={2} value={b.text} onChange={v => edit(x => { x.entries[i].bullets[n].text = v.target.value })} /><button className="plain" onClick={() => edit(x => { x.entries[i].bullets.splice(n, 1) })}>Remove</button></div>)}
      <div className="acts"><button className="plain" onClick={() => edit(x => { x.entries[i].bullets.push({ id: '', text: '' }) })}>Add fact</button><button className="plain" onClick={() => edit(x => { x.entries.splice(i, 1) })}>Remove entry</button></div></div>)}
      {d.skills.map((s, i) => <Field key={i} label={`Skills: ${s.group}`} hint="Separate with commas"><input value={s.items.join(', ')} onChange={v => edit(x => { x.skills[i].items = v.target.value.split(/,\s*/) })} /></Field>)}
      <div className="acts"><button onClick={() => edit(x => { x.entries.push({ id: '', kind: 'project', title: '', organisation: '', location: '', dates: '', bullets: [] }) })}>Add entry</button>
        <button className="primary" disabled={!!act.busy} onClick={() => act.run('c', async () => { await api('/cv', 'PUT', { cv: d, confirmed: true }); reload(); setOpen(false) })}>Confirm these facts</button></div></>}</div>
}
