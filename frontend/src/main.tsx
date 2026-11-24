import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, NavLink, Route, Routes } from 'react-router-dom'
import { Job, useApi } from './api'
import { PlanUse, Usage } from './components/ui'
import Auto from './pages/Auto'
import Discover from './pages/Discover'
import Insights from './pages/Insights'
import JobPage from './pages/JobPage'
import Pipeline from './pages/Pipeline'
import Profile from './pages/Profile'
import Review from './pages/Review'
import './style.css'

type St = { discover: { running: boolean; phase: string; done: number; total: number; errors: string[]; last: any }; claude: { ok: boolean; detail: string }; usage?: Usage; cv: boolean; cv_confirmed: boolean; running: number; auto?: { running: boolean; phase: string; done: number; total: number; waiting: string | null } }

function Shell() {
  const { data: s } = useApi<St>('/status', 4000)
  const { data: jobs } = useApi<Job[]>('/jobs', 10000)
  const review = jobs?.filter(j => j.status === 'needs_review').length ?? 0
  return <div className="shell">
    <nav aria-label="Main"><h1>Applications</h1>
      <NavLink to="/" end>Pipeline</NavLink>
      <NavLink to="/review">Review{review > 0 && <b className="count">{review}</b>}</NavLink>
      <NavLink to="/discover">Discover</NavLink><NavLink to="/auto">Auto-tailor</NavLink><NavLink to="/insights">Insights</NavLink><NavLink to="/profile">Profile</NavLink>
      {s && <div className="foot">
        {s.discover.running && <p>{s.discover.phase === 'web search' ? 'Searching the web' : s.discover.phase === 'reading pages' ? 'Reading pages' : 'Finding roles'}{s.discover.total ? ` ${s.discover.done}/${s.discover.total}` : ''}…</p>}
        {s.auto?.running && <p><Link to="/auto">{s.auto.waiting ? 'Auto-tailor waiting' : s.auto.phase === 'tailoring' ? `Tailoring ${s.auto.done}/${s.auto.total}` : `${s.auto.phase === 'rating' ? 'Rating' : 'Reading adverts'} ${s.auto.done}/${s.auto.total}`}</Link></p>}
        {s.running > 0 && <p>{s.running} task{s.running > 1 ? 's' : ''} running…</p>}
        <PlanUse u={s.usage} /></div>}
    </nav>
    <main>
      {s && (!s.claude.ok || !s.cv_confirmed) && <div className="setup">
        {!s.claude.ok && <p>{s.claude.detail}</p>}
        {!s.cv_confirmed && <p>{s.cv ? 'Your CV facts are waiting to be confirmed.' : 'Your CV has not been added yet.'} <Link to="/profile">Open Profile</Link></p>}
      </div>}
      <Routes>
        <Route path="/" element={<Pipeline />} /><Route path="/review" element={<Review />} /><Route path="/discover" element={<Discover />} />
        <Route path="/auto" element={<Auto />} /><Route path="/insights" element={<Insights />} /><Route path="/profile" element={<Profile />} /><Route path="/job/:id" element={<JobPage />} />
        <Route path="*" element={<p>Page not found.</p>} />
      </Routes>
    </main></div>
}
createRoot(document.getElementById('root')!).render(<BrowserRouter><Shell /></BrowserRouter>)
