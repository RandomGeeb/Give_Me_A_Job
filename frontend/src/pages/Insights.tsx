import { FAMILY, Likelihood, STATUS, useApi } from '../api'
import { FIT } from '../components/ui'
import { Bars, Ranges } from '../components/charts'
import { Empty, Err } from '../components/ui'

type Ins = { likelihood: Likelihood; funnel: { stage: string; n: number }[]; families: { family: string; submitted: number; resolved: number; interviews: number; offers: number }[]
  calls: { purpose: string; calls: number }[]; sources: { source: string; n: number }[]; boards: { total: number; ok: number } }
const WHAT: Record<string, string> = { parse_cv: 'Reading your CV', score: 'Scoring', rate: 'Quick ratings', tailor: 'Tailoring', cover: 'Cover letters', company_search: 'Company search', company_research: 'Company research', answers: 'Answers', review: 'AI review', web_search: 'Web search', read_pages: 'Reading pages' }

export default function Insights() {
  const { data: d, error } = useApi<Ins>('/insights')
  if (!d) return <Err e={error} />
  const bands = Object.entries(d.likelihood.bands).reverse(), submitted = d.funnel.find(f => f.stage === 'submitted')?.n ?? 0
  return <><header className="page"><div><h2>Insights</h2><p className="muted">{d.likelihood.basis === 'assumption-based' ? `Likelihoods are assumptions until ${d.likelihood.needed} applications have an outcome. You have ${d.likelihood.resolved}.` : 'Likelihoods reflect your own outcomes.'}</p></div></header>
    <div className="charts">
      <figure><figcaption>How far applications have got</figcaption>{d.funnel.some(f => f.n) ? <Bars rows={d.funnel.map(f => ({ label: STATUS[f.stage], value: f.n }))} /> : <Empty title="No activity yet" />}</figure>
      <figure><figcaption>Chance of a first interview, by rating</figcaption><Ranges rows={bands.map(([b, v]) => ({ label: FIT[b] ?? b, est: v.interview }))} /></figure>
      <figure><figcaption>Chance of an offer, by rating</figcaption><Ranges rows={bands.map(([b, v]) => ({ label: FIT[b] ?? b, est: v.offer }))} /></figure>
      <figure><figcaption>Claude calls by step, all time</figcaption>{d.calls.length ? <Bars rows={d.calls.map(s => ({ label: WHAT[s.purpose] ?? s.purpose, value: s.calls }))} /> : <Empty title="No calls yet" />}</figure>
    </div>
    <h3>Results by role type</h3>{submitted === 0 ? <p className="muted">Appears once you mark an application as submitted.</p> : <div className="scroll"><table><thead><tr><th>Role type</th><th className="num">Submitted</th><th className="num">With an outcome</th><th className="num">Interviews</th><th className="num">Offers</th></tr></thead>
      <tbody>{d.families.map(f => <tr key={f.family}><td>{FAMILY[f.family] ?? f.family}</td><td className="num">{f.submitted}</td><td className="num">{f.resolved}</td><td className="num">{f.interviews}</td><td className="num">{f.offers}</td></tr>)}</tbody></table></div>}
    <h3>Where roles come from</h3><p>{d.sources.map(s => `${s.source} ${s.n}`).join(', ') || 'No roles yet'}. {d.boards.ok} of {d.boards.total} company boards answered last time.</p></>
}
