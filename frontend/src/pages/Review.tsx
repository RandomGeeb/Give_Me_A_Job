import { Link } from 'react-router-dom'
import { Job, STATUS, useApi } from '../api'
import { Deadline, Empty, Err, Rating } from '../components/ui'

export default function Review() {
  const { data: jobs, error } = useApi<Job[]>('/jobs', 5000)
  if (!jobs) return <Err e={error} />
  const q = jobs.filter(j => ['needs_review', 'tailoring', 'approved', 'filling'].includes(j.status))
    .sort((a, b) => (a.deadline ?? '9999').localeCompare(b.deadline ?? '9999'))
  return <><header className="page"><div><h2>Review</h2><p className="muted">Nothing is filled in until you approve it, and you press submit yourself.</p></div></header>
    {q.length === 0 ? <Empty title="Nothing to review">Tailor a shortlisted role and it will appear here.</Empty> :
      <ul className="list">{q.map(j => <li key={j.id}><Link to={`/job/${j.id}`} className="row"><span className="grow"><strong>{j.company}</strong> {j.title}</span><Rating j={j} /><Deadline d={j.deadline} /><span className={`pill s-${j.status}`}>{STATUS[j.status]}</span></Link></li>)}</ul>}</>
}
