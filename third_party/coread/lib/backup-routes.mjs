import fs from 'fs';
import path from 'path';
import { getDbPath } from './db.mjs';
import { exportBackup, previewBackup, restoreBackup, MAX_BACKUP_BYTES } from './backup.mjs';

export async function handleBackupRequest(req, res) {
  if (!['/v1/backup/export', '/v1/backup/preview', '/v1/backup/restore'].includes(req.url)) return false;
  const send = (status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(body));
  };
  // Deliberately outside the legacy wildcard-CORS API; browsers must use same-origin JSON.
  if (req.method !== 'POST' || req.headers['x-coread-backup'] !== '1' ||
      !String(req.headers['content-type'] || '').startsWith('application/json')) {
    send(403, { error: 'Same-origin backup request required' }); return true;
  }
  try {
    if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) {
      send(403, { error: 'Cross-origin backup request rejected' }); return true;
    }
    if (Number(req.headers['content-length']) > MAX_BACKUP_BYTES) throw Object.assign(new Error('Backup exceeds 64 MiB'), { status: 413 });
    let size = 0;
    const chunks = [];
    for await (const chunk of req) {
      size += Buffer.byteLength(chunk);
      if (size > MAX_BACKUP_BYTES) throw Object.assign(new Error('Backup exceeds 64 MiB'), { status: 413 });
      chunks.push(Buffer.from(chunk));
    }
    let body;
    try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { throw Object.assign(new Error('Invalid JSON backup'), { status: 400 }); }
    if (req.url.endsWith('/export')) send(200, exportBackup(body.settings));
    else if (req.url.endsWith('/preview')) send(200, previewBackup(body));
    else send(200, restoreBackup(body.token, body.confirmed));
  } catch (error) { send(error.status || 400, { error: error.status ? error.message : 'Backup operation failed; original data was preserved' }); }
  return true;
}

// Server-stored backups used by the web menu's backup panel.
const BACKUP_FILE_RE = /^coread-backup-[\dTZ-]+\.json$/;
const previewCounts = new Map();

export async function handleStoredBackupRequest(req, res, ownerKey) {
  const send = (status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(body));
  };
  if (req.method !== 'POST') { send(405, { error: 'POST required' }); return true; }
  if ((req.headers['x-owner-key'] || '') !== ownerKey) { send(403, { error: 'owner_key_required' }); return true; }
  try {
    if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) {
      send(403, { error: 'Cross-origin backup request rejected' }); return true;
    }
    const dir = path.join(path.dirname(getDbPath()), 'backups');
    fs.mkdirSync(dir, { recursive: true });
    let size = 0;
    const chunks = [];
    for await (const chunk of req) {
      size += Buffer.byteLength(chunk);
      if (size > MAX_BACKUP_BYTES) throw Object.assign(new Error('Backup exceeds 64 MiB'), { status: 413 });
      chunks.push(Buffer.from(chunk));
    }
    let body = {};
    if (size) { try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw Object.assign(new Error('Invalid JSON'), { status: 400 }); } }
    const fileOf = name => {
      const f = path.basename(String(name || ''));
      if (!BACKUP_FILE_RE.test(f)) throw Object.assign(new Error('Invalid backup file name'), { status: 400 });
      return path.join(dir, f);
    };
    const action = req.url.split('?')[0].slice('/v1/coread-backup/'.length);
    if (action === 'export') {
      const backup = exportBackup(body.settings || {});
      const file = path.join(dir, `coread-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
      fs.writeFileSync(file, JSON.stringify(backup));
      send(200, { ok: true, file: path.basename(file), bytes: fs.statSync(file).size, createdAt: backup.createdAt });
    } else if (action === 'list') {
      const files = fs.readdirSync(dir).filter(f => BACKUP_FILE_RE.test(f))
        .map(f => { const st = fs.statSync(path.join(dir, f)); return { file: f, bytes: st.size, mtime: st.mtime.toISOString() }; })
        .sort((a, b) => b.mtime.localeCompare(a.mtime));
      send(200, { ok: true, files });
    } else if (action === 'delete') {
      fs.unlinkSync(fileOf(body.file));
      send(200, { ok: true });
    } else if (action === 'preview') {
      const backup = typeof body.file === 'string' ? JSON.parse(fs.readFileSync(fileOf(body.file), 'utf8')) : body.backup;
      const summary = previewBackup(backup);
      previewCounts.set(summary.token, { books: summary.books, comments: summary.comments });
      send(200, summary);
    } else if (action === 'restore') {
      const counts = previewCounts.get(body.token);
      const result = restoreBackup(body.token, body.confirmed);
      previewCounts.delete(body.token);
      send(200, { ...result, counts });
    } else send(404, { error: 'Unknown backup endpoint' });
  } catch (error) { send(error.status || 400, { error: error.status ? error.message : 'Backup operation failed; original data was preserved' }); }
  return true;
}
