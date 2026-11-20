export function localDay(time) {
  const d = new Date(time);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// Split at local calendar boundaries, including DST days. No activity/click timeout.
export function splitDays(start, end) {
  const parts = [];
  while (start < end) {
    const next = new Date(start);
    next.setHours(24, 0, 0, 0);
    const stop = Math.min(end, next.getTime());
    parts.push({ date: localDay(start), ms: stop - start });
    start = stop;
  }
  return parts;
}

export class ReadingClock {
  constructor(session, bookId, persist) {
    this.session = session;
    this.bookId = bookId;
    this.persist = persist;
    this.days = new Map();
    this.dayStart = new Map();
    this.last = null;
    this.total = 0;
  }
  resume(now) { if (this.last === null) this.last = now; }
  sample(now) {
    if (this.last === null) return;
    const start = this.last;
    this.last = now;
    // A suspended JS runtime cannot prove foreground time; never infer a sleep gap.
    if (now <= start || now - start > 5000) return;
    let cursor = start;
    for (const part of splitDays(start, now)) {
      // Clock-chart start = when timing actually began on this day, not when the book was opened.
      if (!this.dayStart.has(part.date)) { const t = new Date(cursor); this.dayStart.set(part.date, t.getHours() * 60 + t.getMinutes()); }
      cursor += part.ms;
      const elapsed = (this.days.get(part.date) || 0) + part.ms;
      this.persist({ session_id: this.session, book_id: this.bookId, reading_date: part.date, elapsed_ms: elapsed, start_min: this.dayStart.get(part.date) });
      this.days.set(part.date, elapsed);
      this.total += part.ms;
    }
  }
  pause(now) { this.sample(now); this.last = null; }
}
