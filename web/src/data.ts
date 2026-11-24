// Types + pure analytics over the exported dashboard.json. No UI here.

export type Sentiment = 'positive' | 'neutral' | 'negative'

export interface Review {
  id: string
  property: string
  score: number
  date: string // YYYY-MM-DD, Australia/Sydney
  reviewedAt: string
  title: string | null
  liked: string | null
  disliked: string | null
  lang: string | null
  guest: string | null
  country: string | null
  travellerType: string | null
  room: string | null
  nights: number | null
  checkin: string | null
  reply: string | null
  sentiment: Sentiment
  complaints: string[]
  praise: string[]
  classifiable: boolean
  firstSeen: string
}

export interface PropertyInfo {
  id: string
  name: string
  shortName: string
  url: string
  bookingScore: number | null
  bookingReviewCount: number | null
  categoryScores: { key: string; label: string; score: number }[]
  storedReviews: number
  lastRun: { status: string; finishedAt: string; inserted: number; error: string | null } | null
  lastSuccessAt: string | null
}

export interface DashboardData {
  generatedAt: string
  timezone: string
  thresholds: { positiveMin: number; negativeMax: number }
  topics: { key: string; label: string }[]
  properties: PropertyInfo[]
  reviews: Review[]
}

// ---------------------------------------------------------------- dates
// All dates are plain YYYY-MM-DD strings in hotel-local time; we do day math in UTC
// on those strings so the viewer's own timezone never shifts a review into another week.
const DAY = 86_400_000
const toMs = (d: string) => Date.parse(d + 'T00:00:00Z')
export const fromMs = (ms: number) => new Date(ms).toISOString().slice(0, 10)
export const addDays = (d: string, n: number) => fromMs(toMs(d) + n * DAY)

export function todayIn(tz: string): string {
  // en-CA formats as YYYY-MM-DD
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date())
}

/** Monday of the week containing d. */
export function weekStart(d: string): string {
  const dow = new Date(toMs(d)).getUTCDay() // 0 = Sun
  return addDays(d, -((dow + 6) % 7))
}
export const monthStart = (d: string) => d.slice(0, 7) + '-01'

export function fmtDate(d: string, opts: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'short' }) {
  return new Date(toMs(d)).toLocaleDateString('en-AU', { ...opts, timeZone: 'UTC' })
}

// ---------------------------------------------------------------- stats
export interface Stats {
  n: number
  avg: number | null
  positive: number
  neutral: number
  negative: number
}

export function stats(rs: Review[]): Stats {
  const s: Stats = { n: rs.length, avg: null, positive: 0, neutral: 0, negative: 0 }
  let sum = 0
  for (const r of rs) {
    sum += r.score
    s[r.sentiment]++
  }
  if (rs.length) s.avg = sum / rs.length
  return s
}

export const inRange = (rs: Review[], from: string, to: string) =>
  rs.filter((r) => r.date >= from && r.date <= to)

export const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0)

// ---------------------------------------------------------------- trends
export interface TrendPoint {
  bucket: string
  label: string
  avg: number | null
  n: number
  positive: number
  neutral: number
  negative: number
}

export function trend(rs: Review[], from: string, to: string): TrendPoint[] {
  const spanDays = (toMs(to) - toMs(from)) / DAY
  const weekly = spanDays <= 190
  const keyOf = weekly ? weekStart : monthStart
  const buckets: string[] = []
  let cur = keyOf(from)
  while (cur <= to) {
    buckets.push(cur)
    cur = weekly ? addDays(cur, 7) : fromMs(Date.UTC(+cur.slice(0, 4), +cur.slice(5, 7), 1))
  }
  const groups = new Map<string, Review[]>(buckets.map((b) => [b, []]))
  for (const r of rs) groups.get(keyOf(r.date))?.push(r)
  return buckets.map((b) => {
    const s = stats(groups.get(b) ?? [])
    return {
      bucket: b,
      label: weekly ? fmtDate(b) : fmtDate(b, { month: 'short', year: '2-digit' }),
      avg: s.avg === null ? null : Math.round(s.avg * 100) / 100,
      n: s.n,
      positive: s.positive,
      neutral: s.neutral,
      negative: s.negative,
    }
  })
}

// ---------------------------------------------------------------- topics
export interface TopicRow {
  key: string
  label: string
  complaints: number
  praise: number
  negShare: number // % of negative reviews whose complaint mentions this topic
  negCount: number
  prevNegShare: number | null
}

export function topicBreakdown(
  rs: Review[],
  prev: Review[],
  topics: { key: string; label: string }[],
): TopicRow[] {
  const negs = rs.filter((r) => r.sentiment === 'negative' && r.classifiable)
  const prevNegs = prev.filter((r) => r.sentiment === 'negative' && r.classifiable)
  return topics
    .map((t) => {
      const negCount = negs.filter((r) => r.complaints.includes(t.key)).length
      const prevCount = prevNegs.filter((r) => r.complaints.includes(t.key)).length
      return {
        key: t.key,
        label: t.label,
        complaints: rs.filter((r) => r.complaints.includes(t.key)).length,
        praise: rs.filter((r) => r.praise.includes(t.key)).length,
        negCount,
        negShare: pct(negCount, negs.length),
        prevNegShare: prevNegs.length ? pct(prevCount, prevNegs.length) : null,
      }
    })
    .sort((a, b) => b.complaints - a.complaints)
}
