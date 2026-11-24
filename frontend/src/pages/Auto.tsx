import { Link } from 'react-router-dom'
import { api, useApi } from '../api'
import { Err, PlanUse, Usage, Tier, TIER, useAction } from '../components/ui'

type Item = { id: number; company: string; title: string; tier: number }
type St = {
  running: boolean; mode: string | null; phase: string; done: number; total: number; waiting: string | null; stopping: boolean; message: string | null; errors: string[]
  job: (Item & { since: number; step: string | null }) | null; finished: (Item & { result: string; ok: boolean; seconds: number })[]
  tiers: { tier: number; total: number; tailored: number; queued: number }[]; unrated: number; held: number; bare: number; twins?: { id: number; company: string; title: string; twin: string }[]; next: Item[]
}
const mins = (s: number) => s < 90 ? `${Math.round(s)} sec` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} hours`
const PHASE: Record<string, string> = { 'reading adverts': 'Reading adverts', rating: 'Rating roles', tailoring: 'Tailoring applications' }

export default function Auto() {
  const { data: s, error, reload } = useApi<St>('/auto', 2000)
  const { data: st } = useApi<{ usage?: Usage; stop_percent?: number }>('/status', 5000)
  const act = useAction()
  if (!s) return <Err e={error} />
  const go = (mode: string) => act.run(mode, async () => { await api('/auto', 'POST', { mode }); await reload() })
  const queued = s.tiers.reduce((n, t) => n + t.queued, 0)
  const took = s.finished.filter(f => f.result.startsWith('ready to review')).map(f => f.seconds)
  const each = took.length ? took.reduce((a, b) => a + b, 0) / took.length : 0
  const share = s.total ? Math.min(1, s.done / s.total) : 0
  return <><header className="page"><div><h2>Auto-tailor</h2><p className="muted">Shortlists and tailors roles rated 1, then 2, then 3. You still review, approve and submit each one.</p></div>
    <div className="acts">{s.running ? <button disabled={s.stopping || !!act.busy} onClick={() => act.run('x', async () => { await api('/auto/stop', 'POST'); await reload() })}>{s.stopping ? 'Stopping after this step…' : 'Stop'}</button> : <>
      <button disabled={!s.unrated || !!act.busy} onClick={() => go('rate')}>{s.unrated ? `Rate ${s.unrated} role${s.unrated > 1 ? 's' : ''}` : 'All roles rated'}</button>
      <button className="primary" disabled={!(queued || s.unrated) || !!act.busy} onClick={() => go('tailor')}>Start auto-tailoring</button></>}</div></header>
    <Err e={act.error} close={act.clear} />
    {s.running ? <section className="panel" aria-label="Progress">
      <div className="prog-h"><strong>{PHASE[s.phase] ?? 'Starting'}</strong><span className="num">{s.done} of {s.total}</span></div>
      <div className="prog" role="progressbar" aria-label={PHASE[s.phase] ?? 'Progress'} aria-valuemin={0} aria-valuemax={s.total} aria-valuenow={s.done}><i style={{ width: `${share * 100}%` }} /></div>
      {s.waiting ? <p className="due">Waiting: {s.waiting} It carries on by itself.{s.waiting.startsWith('Daily limit') && <> <Link to="/profile">Change the daily limit</Link></>}</p> : s.job ? <p><Tier t={s.job.tier} /> <Link to={`/job/${s.job.id}`}><strong>{s.job.company}</strong> {s.job.title}</Link><br /><span role="status">{s.job.step ?? 'Starting'}… <span className="muted">{mins(Math.max(0, Date.now() / 1000 - s.job.since))}</span></span></p> : null}
      {s.phase === 'tailoring' && each > 0 && s.total > s.done && <p className="small muted">About {mins(each)} each, so {mins(each * (s.total - s.done))} of work left, plus any waiting for plan limits.</p>}
      <p className="small muted">Keep the dashboard running and the Mac awake.</p>
    </section> : s.message && <p className="panel">{s.message}</p>}
    {s.errors.length > 0 && <Err e={s.errors[0]} />}
    <section className="panel"><h3>Claude plan use</h3><PlanUse u={st?.usage} stop={st?.stop_percent} bars /></section>
    <section><h3>Ratings</h3><ul className="tiers">{s.tiers.map(t => <li key={t.tier}><Tier t={t.tier} /><span>{TIER[t.tier][1]}</span>
      <span className="num">{t.total} role{t.total === 1 ? '' : 's'}</span>
      <span className="small muted">{t.tier < 4 ? `${t.tailored} tailored, ${t.queued} queued` : 'Never tailored'}</span></li>)}</ul>
      <p className="small muted">{[s.unrated ? `${s.unrated} not rated yet` : '', s.held ? `${s.held} held back by your limit per company` : '', s.bare ? `${s.bare} need their advert pasted in first` : ''].filter(Boolean).join(' · ')}</p></section>
    {!!s.twins?.length && <section><h3>Left out: already listed</h3><p className="small muted">The same role under another name. Shortlist one on its page to include it anyway.</p><ul className="list">{s.twins.map(t => <li key={t.id} className="row"><span className="grow"><Link to={`/job/${t.id}`}><strong>{t.company}</strong> {t.title}</Link><span className="small muted">Same as the one for {t.twin}</span></span></li>)}</ul></section>}
    {s.next.length > 0 && <section><h3>Up next</h3><ul className="list">{s.next.map(j => <li key={j.id} className="row"><Tier t={j.tier} /><span className="grow"><Link to={`/job/${j.id}`}><strong>{j.company}</strong> {j.title}</Link></span></li>)}</ul></section>}
    {s.finished.length > 0 && <section><h3>This run</h3><ul className="list">{s.finished.map((f, i) => <li key={i} className="row"><Tier t={f.tier} /><span className="grow"><Link to={`/job/${f.id}`}><strong>{f.company}</strong> {f.title}</Link><span className={f.ok ? 'small' : 'small due'}>{f.result[0]?.toUpperCase() + f.result.slice(1)}</span></span><span className="small muted">{mins(f.seconds)}</span></li>)}</ul></section>}
  </>
}
