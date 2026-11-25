import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, day, daysTo, Employer, Sent, STATUS, useApi } from '../api'
import { Empty, Err, Field, per, useAction } from '../components/ui'

type Data = { cap: number; applied: Sent[]; employers: Employer[] }
const PERIODS: [number, string][] = [[365, 'Per recruitment cycle'], [30, 'In any 30 days'], [91, 'In any 3 months'], [183, 'In any 6 months'], [244, 'In any 8 months']]
const going = (e: Employer) => e.live.length + e.drafts.length
/** Most urgent first. */
const rank = (e: Employer) => !e.limit ? 4 : e.left === 0 ? (going(e) ? 0 : 2) : going(e) > e.left! ? 1 : 3

export default function Applied() {
  const { data: d, error, reload } = useApi<Data>('/limits', 8000)
  const act = useAction()
  const [edit, setEdit] = useState<string | null>(null), [adding, setAdding] = useState(false)
  if (!d) return <Err e={error} />
  const within = (n: number) => d.applied.filter(a => daysTo(a.date) > -n).length
  const es = [...d.employers].sort((a, b) => rank(a) - rank(b)), limited = es.filter(e => e.limit), rest = es.filter(e => !e.limit)
  const out = d.applied.filter(a => a.outside).length
  return <><header className="page"><div><h2>Applied</h2><p className="muted"><b>{d.applied.length} applied</b>, {within(7)} in the last 7 days, {within(30)} in the last 30{out ? `. ${out} made outside this dashboard` : ''}</p></div>
    <div className="acts"><button onClick={() => setAdding(!adding)}>Add one I made elsewhere</button></div></header>
    <Err e={act.error} close={act.clear} />
    {adding && <Outside done={() => { setAdding(false); reload() }} />}
    <h3>Employers that limit applications</h3>
    <p className="small muted">Read from their adverts. Limits stated only on an employer's website or form are not found, so set those yourself. A recruitment cycle is counted as the last 12 months.</p>
    {limited.length === 0 ? <Empty title="No limits found yet">They appear once adverts have been read.</Empty> :
      <div className="scroll"><table className="limits"><thead><tr><th>Employer</th><th>Limit</th><th className="num">Sent</th><th className="num">Left</th><th>Roles on the go</th><th></th></tr></thead>
        <tbody>{limited.map(e => <Row key={e.key} e={e} open={edit === e.key} toggle={() => setEdit(edit === e.key ? null : e.key)} save={b => act.run('l', async () => { await api(`/limits/${encodeURIComponent(e.key)}`, 'PUT', b); setEdit(null); await reload() })} />)}</tbody></table></div>}
    <h3>Other employers</h3><p className="small muted">No limit found, so your own cap of {d.cap} applies.</p>
    {rest.length > 0 && <div className="scroll"><table className="limits"><thead><tr><th>Employer</th><th>Limit</th><th className="num">Sent</th><th className="num">Left</th><th>Roles on the go</th><th></th></tr></thead>
      <tbody>{rest.map(e => <Row key={e.key} e={e} open={edit === e.key} toggle={() => setEdit(edit === e.key ? null : e.key)} save={b => act.run('l', async () => { await api(`/limits/${encodeURIComponent(e.key)}`, 'PUT', b); setEdit(null); await reload() })} />)}</tbody></table></div>}
    <h3>Every application</h3>
    {d.applied.length === 0 ? <Empty title="None yet">Mark a role as Submitted and it is counted here.</Empty> :
      <ul className="list">{d.applied.map((a, i) => <li key={i} className="row"><span className="grow">{a.id ? <Link to={`/job/${a.id}`}><strong>{a.company}</strong> {a.title}</Link> : <><strong>{a.company}</strong> {a.title}</>}</span><span className="muted">{day(a.date)}</span>
        {a.outside ? <><span className="pill">Made elsewhere</span><button className="plain" onClick={() => act.run('x', async () => { await api(`/outside/${a.outside}`, 'DELETE'); await reload() })}>Remove</button></> : <span className={`pill s-${a.status}`}>{STATUS[a.status]}</span>}</li>)}</ul>}</>
}

function Row({ e, open, toggle, save }: { e: Employer; open: boolean; toggle: () => void; save: (b: { n: number | null; days?: number }) => void }) {
  const [n, setN] = useState(e.limit?.n ?? 1), [days, setDays] = useState(e.limit?.days ?? 365)
  const roles = [...e.live, ...e.drafts], over = !!e.limit && roles.length > e.left!
  return <><tr><td><strong>{e.company}</strong></td><td>{e.limit ? `${e.limit.n} ${per(e.limit.days)}` : 'None found'}{e.yours && <span className="pill">Set by you</span>}{e.limit?.quote && <small className="muted quote">“{e.limit.quote}”</small>}</td>
    <td className="num">{e.limit ? e.used : e.applied.length}</td><td className="num">{e.limit ? <b className={e.left ? '' : 'due'}>{e.left}</b> : ''}{e.free_on && <small className="muted quote">again {day(e.free_on)}</small>}</td>
    <td>{over && <span className={`pill ${e.left ? 's-needs_review' : 's-rejected'}`}>{e.left ? `Pick ${e.left} of ${roles.length}` : 'Limit reached'}</span>}{roles.map(r => <div key={r.id} className="small"><Link to={`/job/${r.id}`}>{r.title}</Link> <span className="muted">{STATUS[r.status]}</span></div>)}</td>
    <td><button className="plain" aria-expanded={open} onClick={toggle}>Change limit</button></td></tr>
    {open && <tr><td colSpan={6}><div className="acts"><label>Applications allowed <input type="number" min={1} max={50} value={n} onChange={x => setN(+x.target.value)} /></label>
      <select aria-label="Period" value={days} onChange={x => setDays(+x.target.value)}>{(PERIODS.some(p => p[0] === days) ? PERIODS : [...PERIODS, [days, per(days)] as [number, string]]).map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
      <button className="primary" disabled={!(n >= 1)} onClick={() => save({ n, days })}>Save</button><button onClick={() => save({ n: 0 })}>No limit</button>{e.yours && <button onClick={() => save({ n: null })}>Back to what the advert says</button>}</div></td></tr>}</>
}

function Outside({ done }: { done: () => void }) {
  const [f, set] = useState({ company: '', title: '', date: new Date().toISOString().slice(0, 10) })
  const act = useAction()
  const on = (k: keyof typeof f) => (e: { target: { value: string } }) => set({ ...f, [k]: e.target.value })
  return <form className="panel" onSubmit={async e => { e.preventDefault(); if (await act.run('a', () => api('/outside', 'POST', f))) done() }}><Err e={act.error} />
    <div className="grid2"><Field label="Employer"><input required value={f.company} onChange={on('company')} /></Field><Field label="Role"><input required value={f.title} onChange={on('title')} /></Field>
      <Field label="Date sent"><input required type="date" value={f.date} onChange={on('date')} /></Field></div><button className="primary" disabled={!!act.busy}>Add</button></form>
}
