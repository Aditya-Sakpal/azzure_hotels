import { useEffect, useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import {
  addDays, fmtDate, inRange, pct, stats, todayIn, topicBreakdown, trend, weekStart,
  type DashboardData, type Review, type Sentiment, type Stats,
} from './data'
import './App.css'

type Preset = 'this-week' | 'last-week' | '4w' | '3m' | '12m' | 'all' | 'custom'
const PRESETS: { key: Preset; label: string }[] = [
  { key: 'this-week', label: 'This week' },
  { key: 'last-week', label: 'Last week' },
  { key: '4w', label: 'Last 4 weeks' },
  { key: '3m', label: 'Last 3 months' },
  { key: '12m', label: 'Last 12 months' },
  { key: 'all', label: 'All time' },
  { key: 'custom', label: 'Custom…' },
]
const SENT_COLORS: Record<Sentiment, string> = { positive: '#2a78d6', neutral: '#a8a7a0', negative: '#e34948' }
const SENT_LABEL: Record<Sentiment, string> = { positive: 'Positive (8+)', neutral: 'Neutral (6–7.9)', negative: 'Negative (≤5.9)' }

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}data/dashboard.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="state">Could not load review data ({error}). Run <code>python -m scraper export</code>.</div>
  if (!data) return <div className="state">Loading reviews…</div>
  return <Dashboard data={data} />
}

function Dashboard({ data }: { data: DashboardData }) {
  const today = todayIn(data.timezone)
  const [property, setProperty] = useState<string>('all')
  const [preset, setPreset] = useState<Preset>('4w')
  const [custom, setCustom] = useState({ from: addDays(today, -30), to: today })
  const [feedTopic, setFeedTopic] = useState<string>('')

  const propName = (id: string) => data.properties.find((p) => p.id === id)?.shortName ?? id
  const topicLabel = (k: string) => data.topics.find((t) => t.key === k)?.label ?? k

  const earliest = data.reviews.length ? data.reviews[data.reviews.length - 1].date : today
  const [from, to] = useMemo((): [string, string] => {
    const ws = weekStart(today)
    switch (preset) {
      case 'this-week': return [ws, today]
      case 'last-week': return [addDays(ws, -7), addDays(ws, -1)]
      case '4w': return [addDays(today, -27), today]
      case '3m': return [addDays(today, -90), today]
      case '12m': return [addDays(today, -364), today]
      case 'all': return [earliest, today]
      case 'custom': return [custom.from, custom.to]
    }
  }, [preset, custom, today, earliest])
  const spanDays = Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) + 1
  const prevFrom = addDays(from, -spanDays)
  const prevTo = addDays(from, -1)

  const byProp = useMemo(
    () => (property === 'all' ? data.reviews : data.reviews.filter((r) => r.property === property)),
    [data.reviews, property],
  )
  const current = useMemo(() => inRange(byProp, from, to), [byProp, from, to])
  const previous = useMemo(() => inRange(byProp, prevFrom, prevTo), [byProp, prevFrom, prevTo])

  // Weekly KPI is always calendar weeks (Mon–Sun, Sydney time), independent of the period filter.
  const ws = weekStart(today)
  const thisWeek = stats(inRange(byProp, ws, today))
  const lastWeek = stats(inRange(byProp, addDays(ws, -7), addDays(ws, -1)))
  const last4 = stats(inRange(byProp, addDays(today, -27), today))

  const cur = stats(current)
  const prev = stats(previous)
  const topics = useMemo(() => topicBreakdown(current, previous, data.topics), [current, previous, data.topics])
  const trendData = useMemo(() => trend(current, from, to), [current, from, to])
  const unanswered = current.filter((r) => r.sentiment === 'negative' && !r.reply).length
  const periodLabel = preset === 'custom'
    ? `${fmtDate(from)} – ${fmtDate(to)}`
    : PRESETS.find((p) => p.key === preset)!.label.toLowerCase()

  const lastSuccess = data.properties.map((p) => p.lastSuccessAt).filter(Boolean).sort()[0]
  const failing = data.properties.filter((p) => p.lastRun && p.lastRun.status === 'failed')

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Guest Review Insights</h1>
          <p className="muted">
            Azzurro Hotels · Booking.com reviews · {data.reviews.length.toLocaleString()} reviews ·
            data collected {lastSuccess ? new Date(lastSuccess).toLocaleString('en-AU', { dateStyle: 'medium', timeStyle: 'short' }) : 'never'}
          </p>
        </div>
        <div className="filters" role="group" aria-label="Filters">
          <label>
            Property
            <select value={property} onChange={(e) => setProperty(e.target.value)}>
              <option value="all">All properties</option>
              {data.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <label>
            Period
            <select value={preset} onChange={(e) => setPreset(e.target.value as Preset)}>
              {PRESETS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </label>
          {preset === 'custom' && (
            <>
              <label>From<input type="date" value={custom.from} max={custom.to} onChange={(e) => setCustom({ ...custom, from: e.target.value })} /></label>
              <label>To<input type="date" value={custom.to} min={custom.from} max={today} onChange={(e) => setCustom({ ...custom, to: e.target.value })} /></label>
            </>
          )}
        </div>
      </header>

      {failing.length > 0 && (
        <div className="banner warn" role="status">
          ⚠ Latest collection failed for {failing.map((p) => p.shortName).join(', ')} — figures for these properties may be out of date.
        </div>
      )}

      {/* ---------------- This week */}
      <section className="kpis" aria-label="This week">
        <WeekCard thisWeek={thisWeek} lastWeek={lastWeek} last4={last4} />
        <Kpi label="Reviews this week" value={String(thisWeek.n)} sub={`${lastWeek.n} last week`} />
        <Kpi
          label="Negative this week"
          value={`${thisWeek.negative}`}
          sub={thisWeek.n ? `${pct(thisWeek.negative, thisWeek.n)}% of reviews · ${lastWeek.negative} last week` : `${lastWeek.negative} last week`}
          tone={thisWeek.negative > lastWeek.negative ? 'bad' : undefined}
        />
        <Kpi
          label={`Negative reviews without a reply (${periodLabel})`}
          value={String(unanswered)}
          sub={unanswered ? 'Replying publicly helps future bookings' : 'All negative reviews answered'}
          tone={unanswered ? 'bad' : 'good'}
        />
      </section>

      {/* ---------------- Insights */}
      <section className="card">
        <h2>Key insights · {periodLabel}</h2>
        <Insights cur={cur} prev={prev} topics={topics} current={current} periodLabel={periodLabel}
          propName={propName} allProps={property === 'all'} onTopic={setFeedTopic} />
      </section>

      {/* ---------------- Property breakdown */}
      <section className="card">
        <h2>Property breakdown · {periodLabel}</h2>
        <PropertyTable data={data} from={from} to={to} prevFrom={prevFrom} prevTo={prevTo}
          selected={property} onSelect={setProperty} topicLabel={topicLabel} />
      </section>

      {/* ---------------- Trends */}
      <section className="grid2">
        <div className="card">
          <h2>Average rating over time</h2>
          <p className="muted small">{spanDays <= 190 ? 'Weekly' : 'Monthly'} average, Booking.com 1–10 scale. Hover for review counts.</p>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={trendData} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="#ecebe7" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#6b6a66' }} tickLine={false} axisLine={{ stroke: '#d6d5d0' }} minTickGap={16} />
              <YAxis domain={[0, 10]} ticks={[0, 2, 4, 6, 8, 10]} tick={{ fontSize: 11, fill: '#6b6a66' }} tickLine={false} axisLine={false} />
              <Tooltip formatter={(v, _n, item) => [`${v} (${(item.payload as { n: number }).n} reviews)`, 'Avg rating']} />
              <Line type="monotone" dataKey="avg" stroke="#2a78d6" strokeWidth={2} dot={{ r: 3 }} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h2>Positive vs negative reviews</h2>
          <p className="muted small">Number of reviews per {spanDays <= 190 ? 'week' : 'month'} by score band.</p>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={trendData} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid stroke="#ecebe7" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#6b6a66' }} tickLine={false} axisLine={{ stroke: '#d6d5d0' }} minTickGap={16} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: '#6b6a66' }} tickLine={false} axisLine={false} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {(['positive', 'neutral', 'negative'] as Sentiment[]).map((s) => (
                <Bar key={s} dataKey={s} name={SENT_LABEL[s]} stackId="a" fill={SENT_COLORS[s]} stroke="#fff" strokeWidth={1} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* ---------------- Topics */}
      <section className="card">
        <h2>What guests talk about · {periodLabel}</h2>
        <p className="muted small">
          Topics detected in the “disliked” (complaint) and “liked” (praise) parts of each review. Click a topic to read those reviews.
        </p>
        <TopicTable topics={topics} negTotal={cur.negative} onTopic={setFeedTopic} active={feedTopic} />
      </section>

      {/* ---------------- Feed */}
      <section className="card" id="feed">
        <h2>Reviews · {periodLabel}</h2>
        <Feed reviews={current} propName={propName} topicLabel={topicLabel} topics={data.topics}
          topic={feedTopic} setTopic={setFeedTopic} />
      </section>

      <footer className="muted small">
        Source: public Booking.com review pages, collected by the Azzurro review scraper. Sentiment bands:
        positive ≥ {data.thresholds.positiveMin}, negative ≤ {data.thresholds.negativeMax}. Topics are keyword-based (English reviews only).
        Generated {new Date(data.generatedAt).toLocaleString('en-AU')}.
      </footer>
    </div>
  )
}

// ======================================================================= pieces
function fmt(n: number | null, d = 1) { return n === null ? '–' : n.toFixed(d) }

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'good' | 'bad' }) {
  return (
    <div className={`kpi ${tone ?? ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

function WeekCard({ thisWeek, lastWeek, last4 }: { thisWeek: Stats; lastWeek: Stats; last4: Stats }) {
  const delta = thisWeek.avg !== null && lastWeek.avg !== null ? thisWeek.avg - lastWeek.avg : null
  const arrow = delta === null ? '' : delta > 0.05 ? '▲' : delta < -0.05 ? '▼' : '■'
  const tone = delta === null ? '' : delta > 0.05 ? 'good' : delta < -0.05 ? 'bad' : ''
  return (
    <div className="kpi hero">
      <div className="kpi-label">Average rating this week</div>
      <div className="kpi-value">{fmt(thisWeek.avg)}<span className="out-of">/10</span></div>
      <div className={`kpi-sub ${tone}`}>
        {delta === null
          ? thisWeek.n === 0 ? 'No reviews yet this week' : 'No reviews last week to compare'
          : <>{arrow} {delta > 0 ? '+' : ''}{delta.toFixed(1)} vs last week ({fmt(lastWeek.avg)})</>}
      </div>
      <div className="kpi-sub muted">4-week average {fmt(last4.avg)} · {thisWeek.n < 5 && thisWeek.n > 0 ? 'small sample, read with care' : `${thisWeek.n} reviews`}</div>
    </div>
  )
}

function Insights({ cur, prev, topics, current, periodLabel, propName, allProps, onTopic }: {
  cur: Stats; prev: Stats; topics: ReturnType<typeof topicBreakdown>; current: Review[]; periodLabel: string
  propName: (id: string) => string; allProps: boolean; onTopic: (k: string) => void
}) {
  if (!cur.n) return <p className="muted">No reviews in this period.</p>
  const items: React.ReactNode[] = []
  const d = cur.avg !== null && prev.avg !== null ? cur.avg - prev.avg : null
  items.push(
    <>Average rating <b>{fmt(cur.avg)}</b> from <b>{cur.n}</b> reviews
      {d !== null && <> — <span className={d >= 0 ? 'good' : 'bad'}>{d >= 0 ? 'up' : 'down'} {Math.abs(d).toFixed(1)}</span> on the previous period ({fmt(prev.avg)})</>}.</>,
  )
  const top = topics.filter((t) => t.negCount > 0).slice(0, 3)
  const negSorted = [...topics].sort((a, b) => b.negShare - a.negShare).filter((t) => t.negCount > 0)
  if (cur.negative && negSorted.length) {
    const t = negSorted[0]
    items.push(
      <><b>{t.negShare}%</b> of negative reviews {periodLabel} mentioned <TopicLink k={t.key} label={t.label.toLowerCase()} onTopic={onTopic} /> issues
        ({t.negCount} of {cur.negative}){t.prevNegShare !== null && <>, vs {t.prevNegShare}% in the previous period</>}.
        {cur.negative < 5 && <span className="muted"> Small sample.</span>}</>,
    )
  }
  const rising = topics
    .filter((t) => t.prevNegShare !== null && t.negCount >= 3 && t.negShare - (t.prevNegShare ?? 0) >= 10)
    .sort((a, b) => b.negShare - (b.prevNegShare ?? 0) - (a.negShare - (a.prevNegShare ?? 0)))[0]
  if (rising) {
    items.push(<>Rising issue: <TopicLink k={rising.key} label={rising.label} onTopic={onTopic} /> complaints up from {rising.prevNegShare}% to {rising.negShare}% of negative reviews.</>)
  }
  if (top.length) {
    items.push(<>Most common complaints overall: {top.map((t, i) => <span key={t.key}>{i ? ', ' : ''}<TopicLink k={t.key} label={t.label} onTopic={onTopic} /> ({t.complaints})</span>)}.</>)
  }
  const praised = [...topics].sort((a, b) => b.praise - a.praise)[0]
  if (praised?.praise) items.push(<>Most praised: <b>{praised.label}</b> ({praised.praise} reviews).</>)
  if (allProps) {
    const byP = new Map<string, Review[]>()
    for (const r of current) byP.set(r.property, [...(byP.get(r.property) ?? []), r])
    const ranked = [...byP.entries()].filter(([, rs]) => rs.length >= 3)
      .map(([p, rs]) => [p, stats(rs).avg!] as const).sort((a, b) => a[1] - b[1])
    if (ranked.length >= 2) {
      items.push(<>Lowest-rated property: <b>{propName(ranked[0][0])}</b> ({fmt(ranked[0][1])}); highest: <b>{propName(ranked[ranked.length - 1][0])}</b> ({fmt(ranked[ranked.length - 1][1])}).</>)
    }
  }
  return <ul className="insights">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
}

function TopicLink({ k, label, onTopic }: { k: string; label: string; onTopic: (k: string) => void }) {
  return (
    <button className="linkish" onClick={() => { onTopic(k); document.getElementById('feed')?.scrollIntoView({ behavior: 'smooth' }) }}>
      {label}
    </button>
  )
}

function PropertyTable({ data, from, to, prevFrom, prevTo, selected, onSelect, topicLabel }: {
  data: DashboardData; from: string; to: string; prevFrom: string; prevTo: string
  selected: string; onSelect: (p: string) => void; topicLabel: (k: string) => string
}) {
  const rows = data.properties.map((p) => {
    const all = data.reviews.filter((r) => r.property === p.id)
    const cur = inRange(all, from, to)
    const s = stats(cur)
    const ps = stats(inRange(all, prevFrom, prevTo))
    const counts = new Map<string, number>()
    for (const r of cur) for (const t of r.complaints) counts.set(t, (counts.get(t) ?? 0) + 1)
    const topComplaint = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]
    return { p, s, ps, topComplaint }
  })
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Property</th><th className="num">Avg rating</th><th className="num">vs previous</th>
            <th className="num">Reviews</th><th className="num">% positive</th><th className="num">% negative</th>
            <th>Top complaint</th><th className="num">Booking.com score</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ p, s, ps, topComplaint }) => {
            const d = s.avg !== null && ps.avg !== null ? s.avg - ps.avg : null
            return (
              <tr key={p.id} className={selected === p.id ? 'sel' : ''} onClick={() => onSelect(selected === p.id ? 'all' : p.id)} title="Click to filter the dashboard to this property">
                <td><b>{p.name}</b></td>
                <td className="num"><ScoreBadge score={s.avg} /></td>
                <td className={`num ${d === null ? '' : d >= 0 ? 'good' : 'bad'}`}>{d === null ? '–' : `${d >= 0 ? '▲ +' : '▼ '}${d.toFixed(1)}`}</td>
                <td className="num">{s.n}</td>
                <td className="num">{s.n ? `${pct(s.positive, s.n)}%` : '–'}</td>
                <td className="num">{s.n ? `${pct(s.negative, s.n)}%` : '–'}</td>
                <td>{topComplaint ? `${topicLabel(topComplaint[0])} (${topComplaint[1]})` : '–'}</td>
                <td className="num">{p.bookingScore ?? '–'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) return <span className="muted">–</span>
  const cls = score >= 8 ? 'pos' : score <= 5.9 ? 'neg' : 'neu'
  return <span className={`badge ${cls}`}>{score.toFixed(1)}</span>
}

function TopicTable({ topics, negTotal, onTopic, active }: {
  topics: ReturnType<typeof topicBreakdown>; negTotal: number; onTopic: (k: string) => void; active: string
}) {
  const max = Math.max(1, ...topics.map((t) => Math.max(t.complaints, t.praise)))
  return (
    <div className="topics">
      <div className="topic-row head">
        <span>Topic</span><span>Complaints</span><span>Praise</span><span className="num">Share of negative reviews ({negTotal})</span>
      </div>
      {topics.map((t) => (
        <button key={t.key} className={`topic-row ${active === t.key ? 'active' : ''}`}
          onClick={() => { onTopic(active === t.key ? '' : t.key); document.getElementById('feed')?.scrollIntoView({ behavior: 'smooth' }) }}>
          <span className="topic-name">{t.label}</span>
          <span className="bar-cell"><span className="bar neg" style={{ width: `${(t.complaints / max) * 100}%` }} /><span className="bar-val">{t.complaints}</span></span>
          <span className="bar-cell"><span className="bar pos" style={{ width: `${(t.praise / max) * 100}%` }} /><span className="bar-val">{t.praise}</span></span>
          <span className="num">{negTotal ? `${t.negShare}%` : '–'}{t.prevNegShare !== null && negTotal ? <span className="muted small"> (prev {t.prevNegShare}%)</span> : null}</span>
        </button>
      ))}
    </div>
  )
}

const PAGE = 20
function Feed({ reviews, propName, topicLabel, topics, topic, setTopic }: {
  reviews: Review[]; propName: (id: string) => string; topicLabel: (k: string) => string
  topics: { key: string; label: string }[]; topic: string; setTopic: (k: string) => void
}) {
  const [sentiment, setSentiment] = useState<'' | Sentiment>('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<'newest' | 'lowest' | 'highest'>('newest')
  const [onlyUnanswered, setOnlyUnanswered] = useState(false)
  const [shown, setShown] = useState(PAGE)

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const out = reviews.filter((r) =>
      (!sentiment || r.sentiment === sentiment) &&
      (!topic || r.complaints.includes(topic) || r.praise.includes(topic)) &&
      (!onlyUnanswered || !r.reply) &&
      (!needle || [r.title, r.liked, r.disliked, r.room, r.guest].some((t) => t?.toLowerCase().includes(needle))))
    if (sort === 'lowest') out.sort((a, b) => a.score - b.score || b.date.localeCompare(a.date))
    else if (sort === 'highest') out.sort((a, b) => b.score - a.score || b.date.localeCompare(a.date))
    else out.sort((a, b) => b.reviewedAt.localeCompare(a.reviewedAt))
    return out
  }, [reviews, sentiment, topic, q, sort, onlyUnanswered])

  useEffect(() => setShown(PAGE), [reviews, sentiment, topic, q, sort, onlyUnanswered])

  return (
    <>
      <div className="feed-filters">
        <input type="search" placeholder="Search review text…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search reviews" />
        <select value={sentiment} onChange={(e) => setSentiment(e.target.value as '' | Sentiment)} aria-label="Sentiment">
          <option value="">All sentiments</option><option value="positive">Positive</option>
          <option value="neutral">Neutral</option><option value="negative">Negative</option>
        </select>
        <select value={topic} onChange={(e) => setTopic(e.target.value)} aria-label="Topic">
          <option value="">All topics</option>
          {topics.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort">
          <option value="newest">Newest first</option><option value="lowest">Lowest score first</option><option value="highest">Highest score first</option>
        </select>
        <label className="check"><input type="checkbox" checked={onlyUnanswered} onChange={(e) => setOnlyUnanswered(e.target.checked)} /> No hotel reply</label>
      </div>
      <p className="muted small">{filtered.length} reviews{topic && <> about <b>{topicLabel(topic)}</b> <button className="linkish" onClick={() => setTopic('')}>clear</button></>}</p>
      <div className="feed">
        {filtered.slice(0, shown).map((r) => (
          <article key={r.property + r.id} className={`review ${r.sentiment}`}>
            <header>
              <ScoreBadge score={r.score} />
              <div className="review-meta">
                <b>{propName(r.property)}</b> · {fmtDate(r.date, { day: 'numeric', month: 'short', year: 'numeric' })}
                <div className="muted small">
                  {[r.guest, r.country, r.travellerType, r.room, r.nights ? `${r.nights} night${r.nights > 1 ? 's' : ''}` : null].filter(Boolean).join(' · ')}
                </div>
              </div>
            </header>
            {r.title && <h3>“{r.title}”</h3>}
            {r.liked && <p className="liked"><span aria-hidden>＋</span> {r.liked}</p>}
            {r.disliked && <p className="disliked"><span aria-hidden>－</span> {r.disliked}</p>}
            {!r.liked && !r.disliked && !r.title && <p className="muted small">Score only — no written comments.</p>}
            <div className="chips">
              {r.complaints.map((t) => <span key={'c' + t} className="chip neg">{topicLabel(t)}</span>)}
              {r.praise.map((t) => <span key={'p' + t} className="chip pos">{topicLabel(t)}</span>)}
              {!r.classifiable && <span className="chip">Not English, topics not detected</span>}
              {r.reply ? <span className="chip replied">Hotel replied</span> : r.sentiment === 'negative' ? <span className="chip warn">No reply yet</span> : null}
            </div>
            {r.reply && <details><summary className="small">Hotel reply</summary><p className="small">{r.reply}</p></details>}
          </article>
        ))}
      </div>
      {shown < filtered.length && (
        <button className="more" onClick={() => setShown(shown + PAGE)}>Show more ({filtered.length - shown} remaining)</button>
      )}
    </>
  )
}
