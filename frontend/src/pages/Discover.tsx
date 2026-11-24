import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, day, FAMILY, Job, REGION, useApi } from '../api'
import { Deadline, Empty, Err, Field, rating, TIER, useAction } from '../components/ui'

const RANK = ['Strong', 'Competitive', 'Possible', 'Reach']
export default function Discover() {
  const { data: jobs, error, reload } = useApi<Job[]>('/jobs', 6000)
  const [q, setQ] = useState(''); const [fam, setFam] = useState(''); const [reg, setReg] = useState(''); const [tier, setTier] = useState(''); const [show, setShow] = useState<'open' | 'filtered'>('open')
  const [sort, setSort] = useState('fit'); const [limit, setLimit] = useState(60); const [adding, setAdding] = useState(false)
  const act = useAction()
  const { data: st } = useApi<{ web_cap: number; discover: { running: boolean; errors: string[]; last: null | { seen: number; kept: number; calls?: number; pages?: number; refused?: number; stopped?: boolean } } }>('/status', 4000)
  const last = st?.discover.last
  const rows = useMemo(() => {
    const t = q.toLowerCase()
    return (jobs ?? []).filter(j => (show === 'open' ? ['discovered', 'scored'] : ['filtered_out']).includes(j.status) && (!fam || j.family === fam) && (!reg || j.regions.includes(reg)) && (!tier || (tier === 'none' ? !rating(j) : rating(j) === +tier)) &&
      (!t || `${j.company} ${j.title} ${j.locations.join(' ')}`.toLowerCase().includes(t)))
      .sort((a, b) => {
        const fit = (rating(a) ?? 9) - (rating(b) ?? 9) || (RANK.indexOf(a.band ?? '') + 9) % 9 - (RANK.indexOf(b.band ?? '') + 9) % 9
        const post = (b.posted_at ?? '').localeCompare(a.posted_at ?? '')
        return sort === 'deadline' ? (a.deadline ?? '9999').localeCompare(b.deadline ?? '9999') || post : sort === 'posted' ? post : fit || (b.coverage ?? 0) - (a.coverage ?? 0) || post
      })
  }, [jobs, q, fam, reg, tier, show, sort])
  if (!jobs) return <Err e={error} />
  const unscored = rows.filter(j => j.status === 'discovered' && !j.task?.running && !j.task?.error)
  const move = (id: number, to: string) => act.run('m', async () => { await api(`/jobs/${id}/move`, 'POST', { to }); await reload() })
  return <><header className="page"><div><h2>Discover</h2><p className="muted">{Object.entries(REGION).map(([k, v]) => `${jobs.filter(j => ['discovered', 'scored'].includes(j.status) && j.regions.includes(k)).length} in ${v === 'UK' ? 'the UK' : v}`).join(', ')}</p></div>
    <div className="acts"><button onClick={() => setAdding(!adding)}>Add by URL</button><button disabled={st?.discover.running} onClick={() => act.run('d', () => api('/discover', 'POST'))}>Find new roles</button>
      <button disabled={st?.discover.running} title="Uses your Claude plan" onClick={() => act.run('w', () => api('/discover/web', 'POST'))}>Search the web</button>
      <button className="primary" disabled={!unscored.length || !!act.busy} onClick={() => act.run('s', () => api('/score', 'POST', { ids: unscored.slice(0, 10).map(j => j.id) }))}>Score next {Math.min(10, unscored.length)}</button></div></header>
    <Err e={act.error} close={act.clear} />
    {st?.discover.errors.length ? <Err e={st.discover.errors.slice(0, 3).join(' · ')} /> : null}
    {last && !st?.discover.running && <p className="small muted">Last search: {last.kept} new role{last.kept === 1 ? '' : 's'}{last.calls !== undefined ? `, ${last.pages} pages read, ${last.refused} not readable${last.stopped ? ', stopped at its limit' : ''}` : ''}.</p>}
    {adding && <Add done={() => { setAdding(false); reload() }} />}
    <div className="filters"><input type="search" placeholder="Company, role or city" aria-label="Search" value={q} onChange={e => setQ(e.target.value)} />
      <select aria-label="Country" value={reg} onChange={e => setReg(e.target.value)}><option value="">All countries</option>{Object.entries(REGION).map(([k, v]) => <option key={k} value={k}>{v} ({jobs.filter(j => j.regions.includes(k) && ['discovered', 'scored'].includes(j.status)).length})</option>)}</select>
      <select aria-label="Role type" value={fam} onChange={e => setFam(e.target.value)}><option value="">All role types</option>{Object.entries(FAMILY).map(([k, v]) => <option key={k} value={k}>{v} ({jobs.filter(j => j.family === k && (!reg || j.regions.includes(reg)) && ['discovered', 'scored'].includes(j.status)).length})</option>)}</select>
      <select aria-label="Rating" value={tier} onChange={e => setTier(e.target.value)}><option value="">All ratings</option>{[1, 2, 3, 4].map(t => <option key={t} value={t}>{t}: {TIER[t][1]}</option>)}<option value="none">Not rated</option></select>
      <select aria-label="Sort" value={sort} onChange={e => setSort(e.target.value)}><option value="fit">Best fit</option><option value="deadline">Deadline</option><option value="posted">Newest</option></select>
      <select aria-label="Show" value={show} onChange={e => setShow(e.target.value as any)}><option value="open">Open roles</option><option value="filtered">Filtered out</option></select></div>
    {rows.length === 0 ? <Empty title="No roles here">{jobs.length ? 'Try a different search or role type.' : 'Press Find new roles to search the job boards. The first run takes a minute or two.'}</Empty> :
      <ul className="list">{rows.slice(0, limit).map(j => <li key={j.id} className="row"><span className="grow"><Link to={`/job/${j.id}`}><strong>{j.company}</strong> {j.title}</Link>
        <span className="small muted">{j.locations.join(', ')}{j.family ? ` · ${FAMILY[j.family]}` : ''}{j.posted_at ? ` · posted ${day(j.posted_at)}` : ''}</span>
        {j.filter_reason && <span className="small">Filtered: {j.filter_reason}</span>}{j.summary ? <span className="small">{j.summary}</span> : j.triage && !j.filter_reason && <span className="small">{j.triage.reason}</span>}{j.triage?.basis === 'title' && <span className="small due">Rated from its title only: the advert could not be read</span>}
        {j.task?.running && <span className="small">Scoring…</span>}{j.task?.error && <span className="small due">{j.task.error}</span>}</span>
        <select className={`rate t${rating(j) ?? 0}`} aria-label={`Rating for ${j.company}`} title="Change the rating" value={rating(j) ?? ''} onChange={e => act.run('t', async () => { await api(`/jobs/${j.id}/tier`, 'PUT', { tier: +e.target.value }); await reload() })}><option value="" disabled>Not rated</option>{[1, 2, 3, 4].map(t => <option key={t} value={t}>{t}: {TIER[t][1]}</option>)}</select><Deadline d={j.deadline} />
        {show === 'open' ? <span className="acts"><button onClick={() => move(j.id, 'shortlisted')}>Shortlist</button><button className="plain" onClick={() => move(j.id, 'closed')}>Hide</button></span> : <button onClick={() => move(j.id, 'discovered')}>Restore</button>}</li>)}</ul>}
    {rows.length > limit && <button onClick={() => setLimit(limit + 60)}>Show more ({rows.length - limit})</button>}</>
}

function Add({ done }: { done: () => void }) {
  const [f, set] = useState({ url: '', company: '', title: '', location: 'United Kingdom', description: '', deadline: '' })
  const act = useAction()
  const on = (k: keyof typeof f) => (e: { target: { value: string } }) => set({ ...f, [k]: e.target.value })
  return <form className="panel" onSubmit={async e => { e.preventDefault(); if (await act.run('a', () => api('/jobs', 'POST', { ...f, deadline: f.deadline || null }))) done() }}>
    <Err e={act.error} /><div className="grid2"><Field label="Web address"><input required type="url" value={f.url} onChange={on('url')} /></Field><Field label="Company"><input required value={f.company} onChange={on('company')} /></Field>
      <Field label="Role"><input required value={f.title} onChange={on('title')} /></Field><Field label="Location"><input value={f.location} onChange={on('location')} /></Field><Field label="Deadline"><input type="date" value={f.deadline} onChange={on('deadline')} /></Field></div>
    <Field label="Job description" hint="Leave empty to try reading it from the page. Paste it for LinkedIn or Indeed roles."><textarea rows={4} value={f.description} onChange={on('description')} /></Field>
    <button className="primary" disabled={!!act.busy}>Add role</button></form>
}
