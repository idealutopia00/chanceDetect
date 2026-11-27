/** Asia/Shanghai trading-date helpers for same-day defaults, 15:00 cutoff, and weekend alignment. */

export interface ShanghaiNowInfo {
  dateStr: string
  hour: number
  minute: number
  isAfterCutoff1500: boolean
}

export function getShanghaiNow(now: Date = new Date()): ShanghaiNowInfo {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now)

  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '00'
  const year = get('year')
  const month = get('month')
  const day = get('day')
  const hour = Number(get('hour'))
  const minute = Number(get('minute'))

  return {
    dateStr: `${year}-${month}-${day}`,
    hour,
    minute,
    isAfterCutoff1500: hour >= 15,
  }
}

function parseUtcDate(dateStr: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr.trim())
  if (!m) return null
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
}

function formatUtcDate(d: Date): string {
  const y = d.getUTCFullYear()
  const m = String(d.getUTCMonth() + 1).padStart(2, '0')
  const day = String(d.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function isWeekendDate(dateStr: string): boolean {
  const d = parseUtcDate(dateStr)
  if (!d) return false
  const dow = d.getUTCDay()
  return dow === 0 || dow === 6
}

export function weekdayLabel(dateStr: string): string {
  const d = parseUtcDate(dateStr)
  if (!d) return ''
  const labels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return labels[d.getUTCDay()] ?? ''
}

export function nextWeekday(dateStr: string): string {
  const d = parseUtcDate(dateStr)
  if (!d) return dateStr
  do {
    d.setUTCDate(d.getUTCDate() + 1)
  } while (d.getUTCDay() === 0 || d.getUTCDay() === 6)
  return formatUtcDate(d)
}

export function prevWeekday(dateStr: string): string {
  const d = parseUtcDate(dateStr)
  if (!d) return dateStr
  do {
    d.setUTCDate(d.getUTCDate() - 1)
  } while (d.getUTCDay() === 0 || d.getUTCDay() === 6)
  return formatUtcDate(d)
}
