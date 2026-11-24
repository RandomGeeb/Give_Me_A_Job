import { useState } from 'react'
import { Est, MIN_OBS, pct, range } from '../api'

type Tip = { x: number; y: number; value: string; label: string } | null
function useTip() {
  const [tip, set] = useState<Tip>(null)
  const on = (value: string, label: string) => ({
    tabIndex: 0, 'aria-label': `${label}: ${value}`,
    onPointerMove: (e: React.PointerEvent) => set({ x: e.clientX, y: e.clientY, value, label }),
    onPointerLeave: () => set(null), onBlur: () => set(null),
    onFocus: (e: React.FocusEvent) => { const r = e.currentTarget.getBoundingClientRect(); set({ x: r.left + 40, y: r.top, value, label }) },
  })
  const node = tip && <div className="tip" style={{ left: Math.min(tip.x + 14, innerWidth - 220), top: tip.y - 46 }}><b>{tip.value}</b><span>{tip.label}</span></div>
  return { on, node }
}

/** Horizontal bars, one series. Every value is also printed, so nothing depends on hover. */
export function Bars({ rows, fmt = String }: { rows: { label: string; value: number; note?: string }[]; fmt?: (n: number) => string }) {
  const { on, node } = useTip()
  const max = Math.max(1e-9, ...rows.map(r => r.value))
  return <div className="bars">{rows.map(r => <div className="bar-row" key={r.label} {...on(fmt(r.value), r.note ?? r.label)}>
    <span className="bar-l">{r.label}</span><span className="bar-t"><span className="bar-f" style={{ width: `${(r.value / max) * 100}%` }} /></span><span className="bar-v">{fmt(r.value)}</span></div>)}{node}</div>
}

/** Estimated range per band, with the observed rate where there is one. */
export function Ranges({ rows }: { rows: { label: string; est: Est }[] }) {
  const { on, node } = useTip()
  const top = Math.min(1, Math.ceil(Math.max(...rows.map(r => Math.max(r.est.high, r.est.n >= MIN_OBS ? r.est.hits / r.est.n : 0))) * 10) / 10)
  const at = (x: number) => `${(x / top) * 100}%`
  const anyObs = rows.some(r => r.est.n >= MIN_OBS)
  return <div>
    <div className="legend"><span><i className="k-range" />Estimated range (80%)</span><span><i className="k-dot" />Estimate</span>{anyObs && <span><i className="k-obs" />What actually happened</span>}</div>
    <div className="bars">{rows.map(({ label, est }) => {
      const obs = est.n >= MIN_OBS ? est.hits / est.n : null
      return <div className="bar-row" key={label} {...on(range(est), `${label}${est.n ? `, actual ${est.hits} of ${est.n}` : ', no outcomes yet'}`)}>
        <span className="bar-l">{label}</span>
        <span className="rng"><span className="rng-b" style={{ left: at(est.low), width: at(est.high - est.low) }} /><span className="rng-d" style={{ left: at(est.mean) }} />{obs !== null && <span className="rng-o" style={{ left: at(obs) }} />}</span>
        <span className="bar-v">{range(est)}</span></div>
    })}</div>
    <div className="bar-row axis" aria-hidden><span className="bar-l" /><span className="rng-ax"><i>0%</i><i>{pct(top / 2)}</i><i>{pct(top)}</i></span><span className="bar-v" /></div>{node}
  </div>
}
