import { createRequire } from 'module';
import path from 'path';
import fs from 'fs';
import { recomputeAiReadingRows } from './reading-stats.mjs';
const require = createRequire(import.meta.url);
const Database = require('better-sqlite3');

let dbPath = null;

export function initDb(customPath) {
  dbPath = customPath || path.join(process.cwd(), 'data', 'coread.db');
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');

  db.exec(`
    CREATE TABLE IF NOT EXISTS books (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      total_paragraphs INTEGER DEFAULT 0,
      created_at DATETIME DEFAULT (datetime('now')),
      cover_image TEXT
    );
    CREATE TABLE IF NOT EXISTS book_paragraphs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER NOT NULL,
      idx INTEGER NOT NULL,
      content TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS book_comments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER NOT NULL,
      paragraph_idx INTEGER NOT NULL,
      sel_start_idx INTEGER,
      sel_end_idx INTEGER,
      sel_end_para_idx INTEGER,
      selected_text TEXT,
      from_who TEXT DEFAULT 'human',
      content TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now')),
      reply_to INTEGER
    );
    CREATE TABLE IF NOT EXISTS book_progress (
      book_id INTEGER PRIMARY KEY,
      page INTEGER DEFAULT 1,
      paragraph_offset INTEGER DEFAULT 0,
      updated_at DATETIME DEFAULT (datetime('now')),
      last_opened_at DATETIME,
      finished_at TEXT
    );
    CREATE TABLE IF NOT EXISTS reading_daily (
      book_id INTEGER NOT NULL,
      book_title TEXT,
      reading_date TEXT NOT NULL,
      seconds INTEGER NOT NULL DEFAULT 0,
      updated_at DATETIME DEFAULT (datetime('now')),
      PRIMARY KEY (book_id, reading_date)
    );
    CREATE TABLE IF NOT EXISTS reading_record_notes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id INTEGER,
      book_title TEXT,
      reading_date TEXT,
      from_who TEXT DEFAULT 'ai',
      content TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS reading_finished (
      book_id INTEGER PRIMARY KEY,
      book_title TEXT NOT NULL,
      finished_at TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS config (
      key TEXT PRIMARY KEY,
      value TEXT
    );
    CREATE TABLE IF NOT EXISTS reading_checkpoints (
      session_id TEXT NOT NULL,
      book_id INTEGER NOT NULL,
      reading_date TEXT NOT NULL,
      elapsed_ms INTEGER NOT NULL,
      start_min INTEGER,
      PRIMARY KEY (session_id, reading_date)
    );
    CREATE TABLE IF NOT EXISTS reading_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      reader TEXT NOT NULL DEFAULT 'ai',
      session_id TEXT NOT NULL,
      book_id INTEGER NOT NULL,
      book_title TEXT,
      reading_date TEXT NOT NULL,
      start_min INTEGER,
      paragraphs INTEGER NOT NULL DEFAULT 0,
      chars INTEGER NOT NULL DEFAULT 0,
      seconds REAL NOT NULL DEFAULT 0,
      created_at DATETIME DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_reading_log_reader ON reading_log(reader, created_at);
    CREATE TABLE IF NOT EXISTS backup_assets (
      book_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      data BLOB NOT NULL,
      PRIMARY KEY (book_id, name)
    );
  `);
  try { db.exec('ALTER TABLE book_progress ADD COLUMN last_opened_at DATETIME'); } catch {}
  try { db.exec('ALTER TABLE book_progress ADD COLUMN finished_at TEXT'); } catch {}
  try { db.exec('ALTER TABLE book_progress ADD COLUMN paragraph_offset INTEGER DEFAULT 0'); } catch {}
  try { db.exec('ALTER TABLE reading_daily ADD COLUMN book_title TEXT'); } catch {}
  try { db.exec('ALTER TABLE reading_record_notes ADD COLUMN book_title TEXT'); } catch {}
  try { db.exec('ALTER TABLE reading_checkpoints ADD COLUMN start_min INTEGER'); } catch {}
  migrateBookProgressToSingleKey(db);
  migrateAiReadingTime(db);
  db.close();
}

// 273e642 建的库里 book_progress 是 (book_id, reader) 复合主键；d2f36fb 退回单列主键后，
// 那段时间建的库对 ON CONFLICT(book_id) 一律报 "ON CONFLICT clause does not match"。
// 检测到 reader 列就重建成单列主键：每本书留一行，default 读者优先，其次最近更新的。
function migrateBookProgressToSingleKey(db) {
  const cols = db.prepare('PRAGMA table_info(book_progress)').all().map(c => c.name);
  if (!cols.includes('reader')) return;
  const migrate = db.transaction(() => {
    db.exec(`
      CREATE TABLE book_progress_single (
        book_id INTEGER PRIMARY KEY,
        page INTEGER DEFAULT 1,
        paragraph_offset INTEGER DEFAULT 0,
        updated_at DATETIME DEFAULT (datetime('now')),
        last_opened_at DATETIME,
        finished_at TEXT
      );
      INSERT OR IGNORE INTO book_progress_single
        (book_id, page, paragraph_offset, updated_at, last_opened_at, finished_at)
        SELECT book_id, page, paragraph_offset, updated_at, last_opened_at, finished_at
        FROM book_progress
        ORDER BY (reader = 'default') DESC, updated_at DESC;
      DROP TABLE book_progress;
      ALTER TABLE book_progress_single RENAME TO book_progress;
    `);
  });
  try {
    migrate();
    console.log('Migrated book_progress to a single-column primary key');
  } catch (e) {
    console.error('book_progress migration failed, left unchanged:', e.message);
  }
}

function migrateAiReadingTime(db) {
  const key = 'ai_reading_time_real_gap_v1';
  if (db.prepare('SELECT 1 FROM config WHERE key = ?').get(key)) return;
  const rows = db.prepare(`SELECT id, session_id, chars, seconds, created_at FROM reading_log WHERE reader = 'ai' ORDER BY id`).all();
  const updates = recomputeAiReadingRows(rows);
  const update = db.prepare('UPDATE reading_log SET seconds = ? WHERE id = ?');
  db.transaction(() => {
    for (const row of updates) update.run(row.seconds, row.id);
    db.prepare('INSERT INTO config (key, value) VALUES (?, ?)').run(key, new Date().toISOString());
  })();
  if (updates.length) console.log(`Recomputed AI reading time for ${updates.length} reading_log rows`);
}

export function getDb(readonly = false) {
  return new Database(dbPath, { readonly });
}

export function getDbPath() { return dbPath; }

export function getImageDir(bookId) {
  const dir = path.join(path.dirname(dbPath), 'book-images', String(bookId));
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}
