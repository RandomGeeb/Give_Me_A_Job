import { ReactNode, useState } from 'react'
import { daysTo, day } from '../api'

export const TIER: Record<number, [string, string]> = { 1: ['1', 'Best chance'], 2: ['2', 'Possible'], 3: ['3', 'Long shot'], 4: ['4', 'Out of scope'] }
export const Tier = ({ t }: { t: number | null }) => t ? <span className={`tier t${t}`} title={TIER[t][1]} aria-label={`Tier ${t}, ${TIER[t][1]}`}>{t}</span> : <span className="muted small">Not rated</span>
/** Roles scored before ratings existed take theirs from the old score. */
export const rating = (j: { tier: number | null; band: string | null }) => j.tier ?? (j.band ? { Strong: 1, Competitive: 1, Possible: 2, Reach: 3 }[j.band] ?? null : null)
export const Rating = ({ j }: { j: { tier: number | null; band: string | null } }) => { const t = rating(j); return <span className="rating"><Tier t={t} />{t && <span aria-hidden>{TIER[t][1]}</span>}</span> }
/** How the starting assumptions and Insights split each rating. */
export const FIT: Record<string, string> = { Strong: '1, strong match', Competitive: '1, good match', Possible: '2, possible', Reach: '3, long shot' }
export const Deadline = ({ d }: { d: string | null }) => {
  if (!d) return null
  const n = daysTo(d)
  return <span className={n < 0 ? 'muted' : n <= 7 ? 'due' : ''}>{n < 0 ? `Closed ${day(d)}` : n === 0 ? 'Closes today' : n <= 7 ? `${n} day${n > 1 ? 's' : ''} left` : `Closes ${day(d)}`}</span>
}
export type Usage = Record<string, { used: number | null; resets: number; at: number | null }>
const WINDOW: Record<string, string> = { five_hour: '5-hour limit', seven_day: 'Weekly limit' }
const when = (t: number) => { const d = new Date(t * 1000), time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }); return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}, ${time}` }
/** How much of the Claude plan is used, as Claude reported it on the app's last call. */
export const PlanUse = ({ u, stop, bars }: { u?: Usage; stop?: number; bars?: boolean }) => {
  const ks = Object.keys(WINDOW).filter(k => u?.[k])
  if (!u || !ks.length) return <p className="small muted">Claude plan use appears after the first call.</p>
  return <div className="use">{ks.map(k => { const w = u[k]; return <div key={k}>
    <p><b>{WINDOW[k]}</b> {w.used === null ? `reset ${when(w.resets)}. Not used by the app since.` : `${Math.round(w.used * 100)}% used, resets ${when(w.resets)}`}</p>
    {bars && <div className="prog slim" role="progressbar" aria-label={WINDOW[k]} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((w.used ?? 0) * 100)}><i style={{ width: `${Math.min(1, w.used ?? 0) * 100}%` }} />{stop && stop < 100 ? <em style={{ left: `${stop}%` }} title={`The app stops at ${stop}%`} /> : null}</div>}</div> })}
    {bars && <p className="small muted">{stop ? `The app stops at ${stop}%. ` : ''}Figures are from the app's last Claude call, so other Claude use since then is not included.</p>}</div>
}
export const Err = ({ e, close }: { e: string | null; close?: () => void }) => e ? <div className="error" role="alert"><span>{e}</span>{close && <button className="plain" onClick={close}>Dismiss</button>}</div> : null

/** Runs an action, showing progress and any error. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name); setError(null)
    try { await fn(); return true } catch (e) { setError((e as Error).message); return false } finally { setBusy(null) }
  }
  return { busy, error, run, clear: () => setError(null) }
}
export const Empty = ({ title, children }: { title: string; children?: ReactNode }) => <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}</div>
export type Flag = { level: 'missing' | 'vague'; text: string }
export const Field = ({ label, hint, flag, id, children }: { label: string; hint?: string; flag?: Flag; id?: string; children: ReactNode }) =>
  <label className={`field${flag ? ` f-${flag.level}` : ''}`} id={id}><span>{label}{flag && <b>{flag.level === 'missing' ? 'Needed' : 'Check'}</b>}</span>{children}{flag && <small className="why">{flag.text}</small>}{hint && <small>{hint}</small>}</label>
