import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, day, Job, Likelihood, range, STATUS, useApi } from '../api'
import { Deadline, Empty, Err, LimitPill, Rating, useAction } from '../components/ui'

const COLS: [string, string[]][] = [['Shortlisted', ['shortlisted', 'tailoring']], ['Review', ['needs_review']], ['Ready to send', ['approved', 'filling']],
  ['Submitted', ['submitted']], ['Assessment', ['assessment']], ['Interview', ['interview']], ['Offer', ['offer']]]
const DONE = ['rejected', 'withdrawn', 'closed']
const NEXT: Record<string, string[]> = { filling: ['submitted'], approved: ['submitted'], submitted: ['assessment', 'interview', 'rejected'], assessment: ['interview', 'rejected'], interview: ['offer', 'rejected'] }

export default function Pipeline() {
  const { data: jobs, error, reload } = useApi<Job[]>('/jobs', 6000)
  const { data: ins } = useApi<{ likelihood: Likelihood }>('/insights')
  const [view, setView] = useState<'board' | 'table'>('board')
  const act = useAction()
  if (!jobs) return <Err e={error} />
  const active = jobs.filter(j => COLS.some(([, s]) => s.includes(j.status)) || DONE.includes(j.status))
  const applied = jobs.filter(j => ['submitted', 'assessment', 'interview', 'offer', 'rejected'].includes(j.status)).length
  const lk = (j: Job) => j.band && ins ? ins.likelihood.bands[j.band] : null
  const move = (id: number, to: string) => act.run('move', async () => { await api(`/jobs/${id}/move`, 'POST', { to }); await reload() })
  return <>
    <header className="page"><div><h2>Pipeline</h2><p className="muted"><Link to="/applied">{applied} applied</Link>, {jobs.filter(j => j.status === 'interview').length} interviewing, {jobs.filter(j => j.status === 'offer').length} offers</p></div>
      <div className="seg" role="group" aria-label="View"><button aria-pressed={view === 'board'} onClick={() => setView('board')}>Board</button><button aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button></div></header>
    <Err e={act.error} close={act.clear} />
    {active.length === 0 ? <Empty title="Nothing shortlisted yet">Pick roles on <Link to="/discover">Discover</Link> to start an application.</Empty> :
      view === 'board' ? <div className="board">{COLS.map(([name, st]) => {
        const js = active.filter(j => st.includes(j.status))
        return <section key={name} className="col"><h3>{name}<span>{js.length}</span></h3>{js.map(j => {
          const l = lk(j)
          return <article key={j.id} className="card"><Link to={`/job/${j.id}`}><strong>{j.company}</strong><span>{j.title}</span></Link>
            <div className="meta"><Rating j={j} /><Deadline d={j.deadline} />{j.limit?.state !== 'sent' && <LimitPill l={j.limit} />}</div>
            {l && <p className="small muted">Interview {range(l.interview)}, offer {range(l.offer)}</p>}
            {j.task?.running && <p className="small">Working: {j.task.kind}…</p>}{j.task?.error && <p className="small due">{j.task.error}</p>}
            {j.next_action && <p className="small">Next: {j.next_action}</p>}
            {NEXT[j.status] && <select aria-label={`Update ${j.company}`} value="" onChange={e => move(j.id, e.target.value)}><option value="" disabled>Update…</option>{NEXT[j.status].map(s => <option key={s} value={s}>{STATUS[s]}</option>)}</select>}
          </article>})}</section>})}</div>
        : <div className="scroll"><table><thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Rating</th><th>Interview</th><th>Offer</th><th>Deadline</th><th>Next action</th><th>Updated</th></tr></thead>
          <tbody>{active.map(j => { const l = lk(j); return <tr key={j.id}><td><Link to={`/job/${j.id}`}>{j.company}</Link></td><td>{j.title}</td><td>{STATUS[j.status]}</td><td><Rating j={j} /></td>
            <td className="num">{l ? range(l.interview) : ''}</td><td className="num">{l ? range(l.offer) : ''}</td><td><Deadline d={j.deadline} /></td><td>{j.next_action}</td><td className="num">{day(j.updated_at)}</td></tr> })}</tbody></table></div>}
    {view === 'board' && active.some(j => DONE.includes(j.status)) && <details className="done"><summary>Finished ({active.filter(j => DONE.includes(j.status)).length})</summary>
      <ul>{active.filter(j => DONE.includes(j.status)).map(j => <li key={j.id}><Link to={`/job/${j.id}`}>{j.company}, {j.title}</Link> <span className="muted">{STATUS[j.status]}</span></li>)}</ul></details>}
    {ins && active.some(j => j.band) && <p className="small muted note">The chances are {ins.likelihood.basis}: {ins.likelihood.resolved} of {ins.likelihood.needed} outcomes recorded before it reflects your own results.</p>}
  </>
}
