import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { daysTo, FAMILY, Job, REGION, STATUS, useApi } from '../api'
import { Deadline, Empty, Err, LimitPill, rating, Rating, TIER } from '../components/ui'

const IN = ['needs_review', 'approved', 'filling', 'tailoring']
const FLAGS: Record<string, [string, (j: Job) => boolean]> = {
  limited: ['Employer limits applications', j => !!j.limit], pick: ['Must pick between roles', j => j.limit?.state === 'pick' || j.limit?.state === 'taken'],
  stop: ['Limit reached', j => j.limit?.state === 'reached'], soon: ['Closes within 7 days', j => !!j.deadline && daysTo(j.deadline) >= 0 && daysTo(j.deadline) <= 7],
  dated: ['Has a deadline', j => !!j.deadline] }
const SORT: Record<string, string> = { deadline: 'Deadline', fit: 'Best rating', company: 'Company A to Z', updated: 'Latest change' }
const due = (j: Job) => !j.deadline ? '9998' : daysTo(j.deadline) < 0 ? '9999' : j.deadline

export default function Review() {
  const { data: jobs, error } = useApi<Job[]>('/jobs', 5000)
  // Copied to the address, so they are still there when you come back from a role.
  const [p, setP] = useSearchParams()
  const [f, setF] = useState<Record<string, string>>(() => Object.fromEntries(p))
  useEffect(() => { setP(Object.fromEntries(Object.entries(f).filter(([, v]) => v)), { replace: true }) }, [f])
  const get = (k: string) => f[k] ?? ''
  const set = (k: string, v: string) => setF(o => ({ ...o, [k]: v }))
  if (!jobs) return <Err e={error} />
  const all = jobs.filter(j => IN.includes(j.status)), words = get('q').toLowerCase().split(/\s+/).filter(Boolean)
  const tests: Record<string, (j: Job) => boolean> = {
    q: j => { const t = `${j.company} ${j.title} ${j.locations.join(' ')} ${j.summary ?? ''}`.toLowerCase(); return words.every(w => t.includes(w)) },
    status: j => !get('status') || j.status === get('status'), tier: j => !get('tier') || String(rating(j) ?? 'none') === get('tier'),
    region: j => !get('region') || j.regions.includes(get('region')), family: j => !get('family') || j.family === get('family'),
    flag: j => !FLAGS[get('flag')] || FLAGS[get('flag')][1](j) }
  const pass = (j: Job, skip = '') => Object.entries(tests).every(([k, t]) => k === skip || t(j))
  const sort = SORT[get('sort')] ? get('sort') : 'deadline'
  const q = all.filter(j => pass(j)).sort((a, b) => {
    const d = due(a).localeCompare(due(b)), f = (rating(a) ?? 9) - (rating(b) ?? 9), c = a.company.trim().localeCompare(b.company.trim(), 'en', { sensitivity: 'base' })
    return sort === 'fit' ? f || d || c : sort === 'company' ? c || f : sort === 'updated' ? b.updated_at.localeCompare(a.updated_at) : d || f || c
  })
  const on = ['q', 'status', 'tier', 'region', 'family', 'flag'].some(k => get(k))
  // Each option counts what it would show with the other filters as they are.
  const pick = (k: string, label: string, none: string, opts: [string, string, (j: Job) => boolean][]) => {
    const pool = all.filter(j => pass(j, k))
    return <select aria-label={label} value={get(k)} onChange={e => set(k, e.target.value)} className={get(k) ? 'on' : ''}><option value="">{none}</option>{opts.map(([v, t, is]) => <option key={v} value={v}>{t} ({pool.filter(is).length})</option>)}</select>
  }
  return <><header className="page"><div><h2>Review</h2><p className="muted">Nothing is filled in until you approve it, and you press submit yourself.</p></div></header>
    {all.length === 0 ? <Empty title="Nothing to review">Tailor a shortlisted role and it will appear here.</Empty> : <>
      <div className="filters review-f" role="search"><input type="search" placeholder="Company, role or city" aria-label="Search" value={get('q')} onChange={e => set('q', e.target.value)} />
        {pick('status', 'Status', 'All statuses', IN.map(s => [s, STATUS[s], j => j.status === s]))}
        {pick('tier', 'Rating', 'All ratings', [1, 2, 3, 4].map(t => [String(t), `${t}: ${TIER[t][1]}`, j => rating(j) === t]))}
        {pick('region', 'Country', 'All countries', Object.entries(REGION).map(([v, t]) => [v, t, j => j.regions.includes(v)]))}
        {pick('family', 'Role type', 'All role types', Object.entries(FAMILY).map(([v, t]) => [v, t, j => j.family === v]))}
        {pick('flag', 'Limits and deadlines', 'All flags', Object.entries(FLAGS).map(([v, [t, is]]) => [v, t, is]))}
        <select aria-label="Sort" value={sort} onChange={e => set('sort', e.target.value === 'deadline' ? '' : e.target.value)}>{Object.entries(SORT).map(([v, t]) => <option key={v} value={v}>Sort: {t}</option>)}</select></div>
      <p className="small muted found-n" role="status">{on ? `${q.length} of ${all.length} roles` : `${all.length} roles`}{on && <button className="plain" onClick={() => setF(o => ({ sort: o.sort ?? '' }))}>Clear filters</button>}</p>
      {q.length === 0 ? <Empty title="No roles match">Try a different search, or clear the filters.</Empty> :
        <ul className="list">{q.map(j => <li key={j.id}><Link to={`/job/${j.id}`} className="row"><span className="grow"><span><strong>{j.company}</strong> {j.title}</span><span className="small muted">{[j.locations.join(', '), j.family ? FAMILY[j.family] : ''].filter(Boolean).join(' · ')}</span></span><LimitPill l={j.limit} /><Rating j={j} /><Deadline d={j.deadline} /><span className={`pill s-${j.status}`}>{STATUS[j.status]}</span></Link></li>)}</ul>}</>}</>
}
