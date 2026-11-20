export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isValidDate(date) {
  if (!DATE_RE.test(date)) return false;
  const parsed = new Date(`${date}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date;
}

export function shiftDate(date, days) {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function calculateStreaks(rows, today) {
  const dates = [...new Set(rows.filter(row => Number(row.seconds) >= 60).map(row => row.reading_date))].sort();
  const dateSet = new Set(dates);
  let cursor = dateSet.has(today) ? today : shiftDate(today, -1);
  let currentStreak = 0;
  while (dateSet.has(cursor)) {
    currentStreak++;
    cursor = shiftDate(cursor, -1);
  }

  let longestStreak = 0;
  let run = 0;
  let previous = null;
  for (const date of dates) {
    run = previous && shiftDate(previous, 1) === date ? run + 1 : 1;
    longestStreak = Math.max(longestStreak, run);
    previous = date;
  }
  return { currentStreak, longestStreak, readingDays: dates.length };
}

export function mergeReadingBooks(timeRows, finishRows) {
  const books = new Map();
  for (const row of timeRows) books.set(row.id, { ...row, finished_at: null });
  for (const row of finishRows) {
    const current = books.get(row.id) || { id: row.id, title: row.title, total_seconds: 0, last_read_at: null };
    books.set(row.id, { ...current, title: current.title || row.title, finished_at: row.finished_at });
  }
  return [...books.values()].sort((a, b) => String(b.finished_at || b.last_read_at || '').localeCompare(String(a.finished_at || a.last_read_at || '')));
}

// AI reads far faster than 400 chars/minute, so a pure chars estimate can log 80+ hours in a day.
// Each row gets the real gap since the previous row in its session, capped by the chars estimate.
export const AI_CHARS_PER_MIN = 400;
export const AI_FIRST_READ_MAX_SECONDS = 60;

export function aiReadingSeconds(chars, gapSeconds) {
  const byChars = (Number(chars) || 0) / AI_CHARS_PER_MIN * 60;
  if (gapSeconds == null) return Math.min(byChars, AI_FIRST_READ_MAX_SECONDS);
  return Math.min(Math.max(Number(gapSeconds) || 0, 0), byChars);
}

const sqliteUtcMs = (value) => Date.parse(`${String(value).replace(' ', 'T')}Z`);

// rows: reading_log rows ordered by id. Only rows still holding the old chars estimate are rewritten,
// so seconds posted from elsewhere (e.g. /v1/ai-reading-time) stay untouched.
export function recomputeAiReadingRows(rows) {
  const lastSeen = new Map();
  const updates = [];
  for (const row of rows) {
    const at = sqliteUtcMs(row.created_at);
    const prev = lastSeen.get(row.session_id);
    lastSeen.set(row.session_id, at);
    const byChars = (Number(row.chars) || 0) / AI_CHARS_PER_MIN * 60;
    if (Math.abs(Number(row.seconds) - byChars) > 0.01) continue;
    const gap = prev == null || Number.isNaN(at) || Number.isNaN(prev) ? null : (at - prev) / 1000;
    const seconds = aiReadingSeconds(row.chars, gap);
    if (Math.abs(seconds - Number(row.seconds)) > 0.001) updates.push({ id: row.id, seconds });
  }
  return updates;
}
