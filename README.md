# Give Me A Job

Finds tech internships in the UK and Singapore, tailors a CV to each, has every application reviewed, fills the form, and tracks the results.
**You approve every application and you press submit.** Everything is stored on your Mac.

## Set up

```sh
uv sync
uv run playwright install chromium
(cd frontend && pnpm install && pnpm build)
claude auth login           # once, if Claude Code is not signed in
uv run jobbot serve         # opens http://127.0.0.1:8765
```

Then on **Profile**: upload your CV, check and confirm the extracted facts, and fill in study details and right to work for
each country. Choose countries and role types under Limits.

## Use

1. **Discover**: press *Find new roles*, score them, shortlist. *Add by URL* takes anything seen elsewhere.
2. **Auto-tailor**: rates every role 1 (best chance) to 4 (out of scope), then shortlists and tailors 1, 2 and 3 in that
   order, soonest deadline first. Each role gets a full score before any tailoring, and can move tier. It respects your limit
   per company, waits when a plan limit is near, carries on after a restart, and stops at review. Change a rating on Discover
   and it stays changed. To do one by hand instead:
3. **Tailor**: rewrites your CV from confirmed facts only. Code blocks unsupported numbers, names, tools and ownership
   verbs, then an independent AI review runs.
4. **Review**: compare the changes, edit, approve. Any later edit cancels the approval.
5. **Open form**, then **Fill this page**. Green is your data, amber is AI-written, red needs you. Tick declarations and
   submit yourself, then set the status to Submitted.
6. Update outcomes on **Pipeline**. **Insights** adjusts likelihoods as they arrive.

`uv run jobbot discover` searches once. `uv run jobbot schedule on` searches daily at 08:00.

## Good to know

- **Filling can send data before you submit.** Ashby saves each field as it is typed, and a CV can upload when attached.
- Likelihoods start as editable guesses. They are labelled that way until 30 applications have an outcome. No reply
  after 60 days counts as no interview.
- CV facts, study details, work rights, reusable answers and drafted text go to Claude through your own Claude Code
  login; no API key is used. Contact details and personal answers (gender, ethnicity and so on) do not. The text of your
  CV, including any contact details on it, is sent once, when it is first read.
- Calls count towards your Claude plan. The app stops at 80% of the 5-hour or weekly limit, after 150 calls in a day (0 on Profile turns that off), or
  before any paid extra usage; change the first two on Profile. This setup is for your own use only: anyone else
  would need their own Claude login.
- A CV already written for a similar role is reused when it shows at least 75% of what the advert asks for that your facts can
  answer (change it on Profile; 0 always writes a new one). It still goes through every check and the AI review, which can
  send it back. *Tailor again* writes a new one. The same role listed under two company names is queued once.
- LinkedIn and Indeed are not automated. Workday, Oracle and similar sites need you to sign in; filling there is best effort.
- **Search the web** (Discover) uses Claude to find employers' own pages, starting with the role types you have fewest of.
  It is limited to 20 Claude calls per run by default.
  It reads a page only where robots.txt and the site allow it, so sites with bot protection are skipped, and job
  aggregators are left out. Use *Add by URL* for those.
- **Cover letters** start with research: Claude searches, code reads pages the site allows, and only passages found word for
  word on a page are kept (for 30 days). Nothing about you is in those calls. The letter lists its sources; read them before
  you approve. Add your own reasons on the Cover letter tab, as the app will not invent any.
- Some employers forbid AI help. The reviewer flags rules it finds, but read the posting.

## Tests

`uv run pytest` (set `SHOTS=<folder>` to keep dashboard screenshots). The model is replaced in tests, so they cost nothing.
