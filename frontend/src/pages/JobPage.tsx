import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Answer, api, App, CV, day, Detail, Likelihood, range, STATUS, TCV, useApi } from '../api'
import { Deadline, Err, Field, Rating, rating, useAction } from '../components/ui'

const SEV = { block: 'Must fix', warn: 'Check', note: 'Note' }
const BASIS: Record<string, string> = { contact: 'Your profile', profile: 'AI, from your profile', cv: 'AI, from your CV', drafted: 'AI draft', unknown: 'Needs you' }

export default function JobPage() {
  const { id } = useParams()
  const { data: j, error, reload } = useApi<Detail>(`/jobs/${id}`, 3000)
  const { data: ins } = useApi<{ likelihood: Likelihood }>('/insights')
  const [tab, setTab] = useState('')
  const [override, setOverride] = useState('')
  const [filled, setFilled] = useState<{ filled: number; needs_you: string[]; failed: string[]; new_questions: number } | null>(null)
  const act = useAction()
  if (!j) return <Err e={error} />
  const a = j.application, rv = a?.review, l = j.band && ins ? ins.likelihood.bands[j.band] : null
  const busy = !!act.busy || !!j.task?.running
  const post = (name: string, path: string, body?: unknown) => act.run(name, async () => { const r = await api(`/jobs/${id}${path}`, 'POST', body ?? {}); await reload(); return r })
  const t = tab || (a?.cv ? 'review' : 'role')
  const editable = ['needs_review', 'approved', 'filling'].includes(j.status) && !j.task?.running
  const manual = j.moves.filter(m => !['approved', 'filling', 'tailoring', 'needs_review', 'scored', 'filtered_out', 'discovered'].includes(m))
  return <>
    <header className="page"><div><h2>{j.company}</h2><p className="lead">{j.title}</p>
      <p className="muted">{j.locations.join(', ')} · <a href={j.url} target="_blank" rel="noreferrer">View posting</a></p></div>
      <div className="side"><span className={`pill s-${j.status}`}>{STATUS[j.status]}</span><Rating j={j} /><Deadline d={j.deadline} /></div></header>
    {l && <p className="likely"><b>Interview {range(l.interview)}</b> <b>Offer {range(l.offer)}</b> <span className="muted">{ins!.likelihood.basis === 'assumption-based' ? `Assumption-based until ${ins!.likelihood.needed} outcomes are recorded (${ins!.likelihood.resolved} so far)` : 'From your own outcomes'}</span></p>}
    <Err e={act.error} close={act.clear} />{j.task?.error && <Err e={j.task.error} />}
    {j.task?.running && <p className="working" role="status">{j.task.kind === 'tailor' ? `${j.task.step ?? 'Tailoring'}. The whole thing can take a few minutes.` : j.task.kind === 'letter' ? 'Researching the company, writing the letter and reviewing it. This can take a few minutes.' : j.task.kind === 'score' ? 'Scoring…' : 'Reviewing…'}</p>}
    <div className="acts main-acts">
      {['discovered', 'scored'].includes(j.status) && <><button disabled={busy} onClick={() => act.run('s', async () => { await api('/score', 'POST', { ids: [j.id] }); await reload() })}>{j.score ? 'Score again' : 'Score'}</button><button className="primary" disabled={busy} onClick={() => post('m', '/move', { to: 'shortlisted' })}>Shortlist</button></>}
      {['shortlisted'].includes(j.status) && <button className="primary" disabled={busy} onClick={() => post('t', '/tailor')}>Tailor application</button>}
      {j.status === 'needs_review' && <><button className="primary" disabled={busy || (!!a?.blockers && !override.trim())} onClick={() => post('a', '/approve', { override })}>Approve this version</button>
        {a && (a.blockers > 0 || (rv?.ai_stale && j.task?.error)) && <input className="override" aria-label="Reason for overriding" placeholder="Reason for approving despite blocking issues" value={override} onChange={e => setOverride(e.target.value)} />}
        <button disabled={busy} onClick={() => post('r', '/rereview')}>Review again</button><button disabled={busy} onClick={() => post('t', '/tailor')}>Tailor again</button></>}
      {['approved', 'filling'].includes(j.status) && <><button className="primary" disabled={busy} onClick={() => post('o', '/open')}>Open form</button>
        <button className="primary" disabled={busy || j.status !== 'filling'} onClick={() => act.run('f', async () => { setFilled(await api(`/jobs/${id}/fill`, 'POST')); await reload() })}>{act.busy === 'f' ? 'Filling…' : 'Fill this page'}</button></>}
      {manual.length > 0 && <select aria-label="Change status" value="" disabled={busy} onChange={e => post('m', '/move', { to: e.target.value })}><option value="" disabled>Change status…</option>{manual.map(m => <option key={m} value={m}>{STATUS[m]}</option>)}</select>}
    </div>
    {['approved', 'filling'].includes(j.status) && <p className="small muted">Filling can send your CV and answers to the employer's system before you press submit. {j.autofill ? '' : 'Sign in first, then press Fill this page on each step.'} Check every field, tick any declarations, and submit it yourself.</p>}
    {filled && <div className="panel"><strong>{filled.filled} fields filled.</strong>{filled.new_questions > 0 && <p>{filled.new_questions} new question{filled.new_questions > 1 ? 's' : ''} drafted. Approve them below, then fill again.</p>}
      {filled.needs_you.length + filled.failed.length > 0 && <><p>Left for you:</p><ul>{[...filled.needs_you, ...filled.failed].map((x, i) => <li key={i}>{x}</li>)}</ul></>}</div>}
    {j.company_applications > 0 && <p className="small">You have {j.company_applications} other application{j.company_applications > 1 ? 's' : ''} at {j.company} (cap {j.company_cap}).</p>}
    {j.score?.application_limit && <p className="small due">Employer limit: {j.score.application_limit}</p>}
    <div className="tabs" role="tablist">{[['role', 'Role and fit'], ...(a?.cv ? [['review', 'Review'], ['cv', 'CV'], ['answers', 'Answers'], ['letter', 'Cover letter']] : []), ['log', 'Timeline']].map(([k, v]) => <button key={k} role="tab" aria-selected={t === k} onClick={() => setTab(k)}>{v}{k === 'review' && rv && rv.issues.filter(i => i.severity !== 'note').length > 0 && <b className="count">{rv.issues.filter(i => i.severity !== 'note').length}</b>}</button>)}</div>
    {t === 'role' && <Role j={j} reload={reload} />}
    {t === 'review' && rv && <section><p className="muted">{rv.pages} page{rv.pages === 1 ? '' : 's'}, {rv.revisions} automatic revision{rv.revisions === 1 ? '' : 's'}.{rv.ai_stale && ' This has changed since the AI review; press Review again to refresh it.'}</p>
      {rv.issues.length === 0 ? <p>No issues found.</p> : <ul className="issues">{rv.issues.map((i, n) => <li key={n} className={`i-${i.severity}`}><b>{SEV[i.severity]}</b><div><strong>{i.where}</strong><p>{i.problem}</p>{i.suggestion && <p className="muted">{i.suggestion}</p>}</div><small>{i.by === 'code' ? 'Automatic check' : 'AI reviewer'}</small></li>)}</ul>}
      {rv.strengths.length > 0 && <><h3>Strengths</h3><ul>{rv.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul></>}</section>}
    {t === 'cv' && a?.cv && j.cv && <CvDiff key={a.updated_at} from={a.cv_from} t={a.cv} cv={j.cv} id={j.id} stamp={a.updated_at} editable={editable} save={cv => act.run('e', async () => { await api(`/jobs/${id}/application`, 'PUT', { cv }); await reload() })} />}
    {t === 'answers' && a && <Answers key={a.updated_at} answers={a.answers ?? {}} editable={editable} save={x => act.run('e', async () => { await api(`/jobs/${id}/application`, 'PUT', { answers: x }); await reload() })} />}
    {t === 'letter' && a && <Letter key={a.updated_at} a={a} editable={editable} busy={busy} save={x => act.run('e', async () => { await api(`/jobs/${id}/application`, 'PUT', { cover_letter: x }); await reload() })} write={(notes, fresh) => post('l', '/letter', { notes, fresh })} />}
    {t === 'log' && <ul className="log">{[...j.events].reverse().map(e => <li key={e.id}><time>{new Date(e.at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}</time><span>{e.to_status ? STATUS[e.to_status] : e.kind}{e.detail ? `: ${e.detail.replace(/^band (\w+)$/, (_, b) => `rated ${rating({ tier: null, band: b })}`)}` : ''}</span></li>)}</ul>}
  </>
}

function Role({ j, reload }: { j: Detail; reload: () => void }) {
  const [f, set] = useState({ notes: j.notes ?? '', next_action: j.next_action ?? '', deadline: j.deadline ?? '', description: j.description ?? '' })
  const act = useAction()
  useEffect(() => { set(p => ({ ...p, description: p.description || j.description || '', deadline: p.deadline || j.deadline || '' })) }, [j.description, j.deadline])
  return <div className="two"><section>{j.score ? <><p>{j.score.summary}</p>
    <h3>Requirements</h3><ul className="reqs">{j.score.requirements.map((r, i) => <li key={i}><b className={`m-${r.met}`}>{{ yes: 'Met', partial: 'Partly', no: 'Missing', unknown: 'Unknown' }[r.met]}</b><span>{r.requirement}{r.essential ? '' : ' (nice to have)'}<small className="muted">{r.evidence}</small></span></li>)}</ul>
    <h3>Eligibility</h3><p>{{ yes: 'Looks eligible', no: 'Not eligible', unsure: 'Unclear' }[j.score.eligible]}. Competition: {j.score.competition}.</p><ul>{j.score.eligibility_notes.map((n, i) => <li key={i}>{n}</li>)}</ul></> : <p className="muted">Not scored yet.</p>}
    <h3>Posting</h3><div className="desc">{j.description || 'No description yet. It is fetched when you score the role, or you can paste it here.'}</div></section>
    <aside className="panel"><Err e={act.error} /><Field label="Next action"><input value={f.next_action} onChange={e => set({ ...f, next_action: e.target.value })} /></Field><Field label="Deadline"><input type="date" value={f.deadline} onChange={e => set({ ...f, deadline: e.target.value })} /></Field>
      <Field label="Notes"><textarea rows={5} value={f.notes} onChange={e => set({ ...f, notes: e.target.value })} /></Field><Field label="Job description"><textarea rows={6} value={f.description} onChange={e => set({ ...f, description: e.target.value })} /></Field>
      <button disabled={!!act.busy} onClick={() => act.run('p', async () => { await api(`/jobs/${j.id}`, 'PATCH', f); reload() })}>Save</button></aside></div>
}

function CvDiff({ from, t, cv, id, stamp, editable, save }: { from: App['cv_from']; t: TCV; cv: CV; id: number; stamp: string; editable: boolean; save: (t: TCV) => void }) {
  const [d, set] = useState<TCV>(() => structuredClone(t))
  const facts = Object.fromEntries(cv.entries.flatMap(e => e.bullets.map(b => [b.id, b.text])))
  const used = new Set(d.entries.map(e => e.entry_id)), usedFacts = new Set(d.entries.flatMap(e => e.bullets.flatMap(b => b.fact_ids)))
  const dirty = JSON.stringify(d) !== JSON.stringify(t)
  const edit = (fn: (x: TCV) => void) => { const c = structuredClone(d); fn(c); set(c) }
  return <div className="two wide"><section>
    {from?.job_id && <p className="small muted">{from.changed ? 'Adapted' : 'Reused'} from the CV for <Link to={`/job/${from.job_id}`}>{from.company}, {from.title}</Link>, which showed {Math.round((from.match ?? 0) * 100)}% of what this advert asks for that your CV can answer. Press Tailor again for a new one.</p>}
    {d.changes.length > 0 && <><h3>What changed</h3><ul>{d.changes.map((c, i) => <li key={i}>{c}</li>)}</ul></>}
    {d.entries.map((e, ei) => { const src = cv.entries.find(x => x.id === e.entry_id); return <div key={ei} className="diff"><h3>{src?.title}<span className="muted"> {src?.organisation}</span></h3>
      {e.bullets.map((b, bi) => { const orig = b.fact_ids.map(f => facts[f] ?? '(missing)').join(' / '); const same = orig === b.text
        return <div key={bi} className={same ? 'pair same' : 'pair'}>{!same && <p className="was">{orig}</p>}<div className="now">{editable ? <textarea aria-label="Bullet" rows={2} value={b.text} onChange={v => edit(x => { x.entries[ei].bullets[bi].text = v.target.value })} /> : <p>{b.text}</p>}{editable && <button className="plain" onClick={() => edit(x => { x.entries[ei].bullets.splice(bi, 1) })}>Remove</button>}</div></div> })}
      {editable && src?.bullets.filter(b => !usedFacts.has(b.id)).map(b => <p key={b.id} className="left"><span>{b.text}</span><button className="plain" onClick={() => edit(x => { x.entries[ei].bullets.push({ text: b.text, fact_ids: [b.id] }) })}>Put back</button></p>)}</div> })}
    {cv.entries.filter(e => !used.has(e.id)).length > 0 && <><h3>Left out</h3>{cv.entries.filter(e => !used.has(e.id)).map(e => <p key={e.id} className="left"><span>{e.title} {e.organisation}</span>{editable && <button className="plain" onClick={() => edit(x => { x.entries.push({ entry_id: e.id, bullets: e.bullets.map(b => ({ text: b.text, fact_ids: [b.id] })) }) })}>Put back</button>}</p>)}</>}
    <h3>Skills</h3>{d.skills.map((s, i) => <p key={i}><b>{s.group}:</b> {s.items.join(', ')}</p>)}
    {editable && <button className="primary" disabled={!dirty} onClick={() => save(d)}>Save changes</button>}</section>
    <iframe title="Tailored CV" className="pdf" src={`/api/jobs/${id}/cv.pdf?v=${encodeURIComponent(stamp)}`} /></div>
}

function Answers({ answers, editable, save }: { answers: Record<string, Answer>; editable: boolean; save: (x: Record<string, string[]>) => void }) {
  const [d, set] = useState<Record<string, string[]>>({})
  const rows = Object.entries(answers)
  if (!rows.length) return <p className="muted">No form questions have been read yet. They appear after you open the form.</p>
  return <section>{rows.map(([k, a]) => { const v = d[k] ?? a.value, locked = !editable || a.policy === 'consent' || a.policy.startsWith('sensitive') || a.basis === 'contact'
    return <div key={k} className={`ans a-${a.basis}`}><div className="ans-h"><strong>{a.label}{a.required && ' *'}</strong><small>{d[k] ? 'Edited' : BASIS[a.basis]}</small></div>
      {locked ? <p>{v.join(', ') || <span className="muted">{a.note}</span>}</p> : a.options.length && a.options.length < 80 ? (a.kind === 'checkbox' ? <div className="checks">{a.options.map(o => <label key={o}><input type="checkbox" checked={v.includes(o)} onChange={e => set({ ...d, [k]: e.target.checked ? [...v, o] : v.filter(x => x !== o) })} />{o}</label>)}</div>
        : <select aria-label={a.label} value={v[0] ?? ''} onChange={e => set({ ...d, [k]: e.target.value ? [e.target.value] : [] })}><option value="">Leave for me</option>{a.options.map(o => <option key={o}>{o}</option>)}</select>)
        : <textarea aria-label={a.label} rows={a.kind === 'textarea' ? 4 : 1} value={v[0] ?? ''} onChange={e => set({ ...d, [k]: [e.target.value] })} />}
      {!locked && a.note && <small className="muted">{a.note}</small>}</div> })}
    {editable && <button className="primary" disabled={!Object.keys(d).length} onClick={() => save(d)}>Save answers</button>}</section>
}

function Letter({ a, editable, busy, save, write }: { a: App; editable: boolean; busy: boolean; save: (s: string) => void; write: (notes: string, fresh: boolean) => void }) {
  const text = a.cover_letter ?? '', found = [...(a.research ?? [])].sort((x, y) => +y.used - +x.used)
  const [v, set] = useState(text)
  const [notes, setNotes] = useState(a.letter_notes ?? '')
  const n = v.trim() ? v.trim().split(/\s+/).length : 0
  return <div className="two"><section>
    {!text && <p className="muted">No cover letter file was requested when the form was read. Anything here is attached if the form has a cover letter upload.</p>}
    <textarea aria-label="Cover letter" rows={18} value={v} readOnly={!editable} onChange={e => set(e.target.value)} />
    <div className="acts">{editable && <button className="primary" disabled={v === text || busy} onClick={() => save(v)}>Save letter</button>}
      {editable && a.letter_prev && a.letter_prev !== v && <button onClick={() => set(a.letter_prev!)}>Show previous letter</button>}<span className="small muted">{n} words</span></div>
    <h3>Company research</h3>
    {found.length === 0 ? <p className="muted">None yet. Write the letter to look the company up.</p> : <><p className="small muted">Word for word from these pages. Read the ones the letter uses, as an interviewer may ask about them.</p>
      <ul className="found">{found.map(f => <li key={f.id}><strong>{f.topic}</strong>{f.used && <span className="pill">In the letter</span>}<blockquote>{f.quote}</blockquote>
        <small><a href={f.url} target="_blank" rel="noreferrer">{new URL(f.url).hostname.replace(/^www\./, '')}</a>{f.published ? `, ${f.published}` : ''}{f.own_site ? '' : ' (not the company\'s own site)'}</small></li>)}</ul></>}
  </section>
  {editable && <aside className="panel"><label className="field"><span>Your own reasons (optional)</span><textarea aria-label="Your own reasons" rows={7} value={notes} maxLength={2000} onChange={e => setNotes(e.target.value)} /><small>For example why this company, whether you use its product, or who you met. Only true things: the letter treats this as fact.</small></label>
    <button className="primary" disabled={busy || v !== text} onClick={() => write(notes, false)}>{text ? 'Rewrite with research' : 'Write with research'}</button>
    {found.length > 0 && <button disabled={busy || v !== text} onClick={() => write(notes, true)}>Look the company up again</button>}
    <p className="small muted">{v !== text ? 'Save or undo your edits first.' : 'Uses some of your Claude plan. Your CV and answers stay the same, and you approve the application again.'}</p></aside>}</div>
}
