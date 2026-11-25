import { useCallback, useEffect, useRef, useState } from 'react'

export type Task = { kind: string; running: boolean; error: string | null; step?: string | null } | null
export type Walk = { running: boolean; state: string; message: string; step: number; of: number; name: string; pages: { page: string; errors: string[]; filled: number; kept: number
  check: { label: string; note: string; guess: boolean }[]; needs_you: string[]; failed: string[]; left: string[]; draft_error: string | null }[] } | null
export type Limit = { state: 'sent' | 'reached' | 'taken' | 'pick' | 'ok'; n: number; days: number; used: number; left: number; going: number; quote: string; by: 'advert' | 'you'; free_on: string | null; others: string[] } | null
export type Sent = { id: number | null; title: string; company: string; status: string; date: string; outside: string | null }
type Role = { id: number; title: string; status: string }
export type Employer = { key: string; company: string; yours: boolean; limit: { n: number; days: number; quote: string; by: 'advert' | 'you' } | null; advert: { n: number; days: number; quote: string; job: number } | null
  applied: Sent[]; live: Role[]; drafts: Role[]; used: number | null; left: number | null; free_on: string | null }
export type Job = {
  id: number; company: string; title: string; locations: string[]; regions: string[]; url: string; ats: string; source: string; family: string | null
  status: string; band: string | null; filter_reason: string | null; deadline: string | null; posted_at: string | null; next_action: string | null
  first_seen: string; updated_at: string; coverage: number | null; summary: string | null; eligible: string | null; competition: string | null; task: Task
  tier: number | null; triage: { reason: string; basis: 'quick' | 'full' | 'title' | 'code' | 'you' } | null; limit: Limit
}
export type Issue = { severity: 'block' | 'warn' | 'note'; where: string; problem: string; suggestion: string; by: string }
export type Answer = { label: string; kind: string; required: boolean; options: string[]; policy: string; value: string[]; basis: string; note: string }
export type Finding = { id: string; quote: string; topic: string; url: string; title: string; published: string; own_site: boolean; used: boolean }
export type Entry = { id: string; kind: string; title: string; organisation: string; location: string; dates: string; bullets: { id: string; text: string }[] }
export type CV = { name: string; email: string; phone: string; links: string[]; entries: Entry[]; skills: { group: string; items: string[] }[] }
export type TCV = { entries: { entry_id: string; bullets: { text: string; fact_ids: string[] }[] }[]; skills: { group: string; items: string[] }[]; changes: string[] }
export type App = {
  cv: TCV | null; cover_letter: string | null; answers: Record<string, Answer> | null; approved: boolean; blockers: number; has_pdf: boolean
  research: Finding[] | null; letter_notes: string | null; letter_prev: string | null
  cv_from: { job_id?: number; company?: string; title?: string; match?: number; changed?: boolean } | null
  updated_at: string; review: { issues: Issue[]; strengths: string[]; revisions: number; pages: number; ai_stale: boolean } | null
}
export type Score = { eligible: string; eligibility_notes: string[]; competition: string; application_limit: string | null; summary: string; coverage: number | null
  requirements: { requirement: string; essential: boolean; met: string; evidence: string }[] }
export type Detail = Omit<Job, 'coverage'> & { description: string; notes: string; score: Score | null; application: App | null; cv: CV | null; moves: string[]
  events: { id: number; at: string; kind: string; from_status: string; to_status: string; detail: string }[]
  company_applications: Sent[]; company_cap: number; autofill: boolean; walk: Walk; apply_url: string }
export type Est = { mean: number; low: number; high: number; hits: number; n: number }
export type Likelihood = { bands: Record<string, { interview: Est; offer: Est }>; resolved: number; needed: number; basis: string }

export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const form = body instanceof FormData
  const r = await fetch('/api' + path, {
    method, headers: { 'x-jobbot': '1', ...(body && !form ? { 'content-type': 'application/json' } : {}) },
    body: body ? (form ? (body as FormData) : JSON.stringify(body)) : undefined,
  })
  if (!r.ok) {
    const d = await r.json().catch(() => null)
    throw new Error(typeof d?.detail === 'string' ? d.detail : `Request failed (${r.status})`)
  }
  return r.json()
}

/** Load a path and optionally refresh it on an interval. */
export function useApi<T>(path: string, every = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const live = useRef(path)
  live.current = path
  const reload = useCallback(async () => {
    try { const d = await api<T>(path); if (live.current === path) { setData(d); setError(null) } } catch (e) { setError((e as Error).message) }
  }, [path])
  useEffect(() => { reload(); if (!every) return; const t = setInterval(reload, every); return () => clearInterval(t) }, [reload, every])
  return { data, error, reload }
}

export const REGION: Record<string, string> = { uk: 'UK', sg: 'Singapore' }
export const FAMILY: Record<string, string> = { software: 'Software', data_ml_quant: 'Data, ML, quant', project: 'Project', product: 'Product', analyst: 'Analyst' }
export const STATUS: Record<string, string> = {
  discovered: 'New', filtered_out: 'Filtered out', scored: 'Scored', shortlisted: 'Shortlisted', tailoring: 'Tailoring', needs_review: 'Needs review',
  approved: 'Approved', filling: 'Filling', submitted: 'Submitted', assessment: 'Assessment', interview: 'Interview', offer: 'Offer', rejected: 'Rejected',
  withdrawn: 'Withdrawn', closed: 'Closed' }
export const pct = (x: number) => x < 0.01 ? '<1%' : `${Math.round(x * 100)}%`
export const range = (e: Est) => { const a = pct(e.low), b = pct(e.high); return a === b ? a : `${e.low < 0.01 ? a : a.replace('%', '')}–${b}` }
/** Too few outcomes to plot as a rate. */
export const MIN_OBS = 5
export const day = (s: string | null) => s ? new Date(s.length === 10 ? s + 'T12:00:00' : s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : ''
export const daysTo = (s: string) => Math.ceil((new Date(s + 'T23:59:59').getTime() - Date.now()) / 864e5)
