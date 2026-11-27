# -*- coding: utf-8 -*-
"""📝 整点记录本地 WebView2 前端（与沟通页同款纯本地单页风格，支持离线运行）。"""

_HOUR_LOG_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OIT 整点记录</title>
<style>
  :root {
    --bg: #0f131b;
    --bg-card: #171c28;
    --border: #2a3346;
    --text: #e6edf7;
    --text-dim: #8b97ad;
    --accent: #67d5f7;
    --accent-hover: #8be0fa;
    --success: #22c55e;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden;
    background: var(--bg);
    color: var(--text);
    font-family: "Microsoft YaHei UI", "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 13.5px;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }
  .app {
    display: flex; flex-direction: column;
    width: 100%; height: 100%;
    padding: 18px 26px 16px;
    gap: 8px;
  }
  .hdr {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
  }
  .hdr-title {
    font-size: 20px; font-weight: 700; letter-spacing: 0.3px;
  }
  .hdr-right {
    display: flex; align-items: center; gap: 10px;
  }
  .state-text {
    font-size: 13px; font-weight: 700;
  }
  .btn {
    appearance: none; border: 1px solid var(--border);
    background: var(--bg-card); color: var(--text);
    padding: 5px 12px; border-radius: 6px;
    font-size: 12.5px; cursor: pointer; font-family: inherit;
    transition: background .12s, border-color .12s, transform .08s;
  }
  .btn:hover { border-color: var(--accent); background: rgba(127,127,127,0.12); }
  .btn:active { transform: translateY(1px); }
  .date-line {
    color: var(--accent); font-weight: 700; font-size: 13.5px;
    padding: 2px 2px 4px;
  }
  .list-card {
    flex: 1; min-height: 0;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg);
    overflow-y: auto;
    padding: 8px 10px;
    display: flex; flex-direction: column; gap: 5px;
  }
  .row {
    display: flex; align-items: center; gap: 10px;
    padding: 3px 4px; border-radius: 6px;
  }
  .row-label {
    width: 136px; flex-shrink: 0;
    font-weight: 700; font-size: 13px;
    color: var(--text-dim);
    white-space: nowrap;
  }
  .row-label.accent { color: var(--accent); }
  .row-label.active { color: var(--text); }
  .cells {
    flex: 1; min-width: 0;
    display: flex; gap: 8px;
  }
  .cell-input {
    flex: 1; min-width: 0;
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 10px; font-size: 13.5px; font-family: inherit;
    outline: none; user-select: text;
    transition: border-color .12s, background .12s;
  }
  .cell-input:focus { border-color: var(--accent); }
  .cell-input.wait { border-color: var(--accent); }
  .cell-input:disabled {
    background: var(--bg); color: var(--text-dim);
    cursor: default;
  }
  .list-card::-webkit-scrollbar { width: 8px; }
  .list-card::-webkit-scrollbar-track { background: transparent; }
  .list-card::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div id="title" class="hdr-title">📝 整点记录</div>
    <div class="hdr-right">
      <button id="btnSettings" class="btn">⚙ 设置</button>
      <div id="stateText" class="state-text"></div>
    </div>
  </div>
  <div id="dateLine" class="date-line"></div>
  <div id="listCard" class="list-card"></div>
</div>
<script>
const TOKEN = __TOKEN__;
const $ = id => document.getElementById(id);
let pendingScrollSeq = -1;

function applyColors(c) {
  if (!c) return;
  const r = document.documentElement.style;
  r.setProperty('--bg', c.bg);
  r.setProperty('--bg-card', c.bg_card);
  r.setProperty('--border', c.border);
  r.setProperty('--text', c.text);
  r.setProperty('--text-dim', c.text_dim);
  r.setProperty('--accent', c.accent);
  r.setProperty('--accent-hover', c.accent_hover || c.accent);
  r.setProperty('--success', c.success || '#22c55e');
}

async function postAction(payload) {
  try {
    const res = await fetch('/api/action?token=' + encodeURIComponent(TOKEN), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const j = await res.json();
    if (j && j.state) render(j.state);
    return j;
  } catch (_) {
    return null;
  }
}

function render(s) {
  if (!s) return;
  applyColors(s.colors);
  $('title').textContent = s.title || '📝 整点记录';
  $('btnSettings').textContent = s.settings_label || '⚙ 设置';
  const st = $('stateText');
  st.textContent = s.state_text || '';
  st.style.color = s.enabled ? 'var(--success)' : 'var(--text-dim)';
  $('dateLine').textContent = s.date_text || '';

  const activeEl = document.activeElement;
  const activeKey = (activeEl && activeEl.dataset && activeEl.dataset.cellKey) ? activeEl.dataset.cellKey : '';
  const list = $('listCard');
  const existing = new Map();
  list.querySelectorAll('.row').forEach(r => existing.set(r.dataset.row, r));

  (s.rows || []).forEach(rowData => {
    const h = String(rowData.h);
    let rowEl = existing.get(h);
    if (!rowEl) {
      rowEl = document.createElement('div');
      rowEl.className = 'row';
      rowEl.dataset.row = h;
      const lbl = document.createElement('div');
      lbl.className = 'row-label';
      const cells = document.createElement('div');
      cells.className = 'cells';
      rowEl.appendChild(lbl);
      rowEl.appendChild(cells);
      list.appendChild(rowEl);
    }
    const lbl = rowEl.firstChild;
    lbl.textContent = rowData.label || '';
    lbl.className = 'row-label' + (rowData.label_tone ? ' ' + rowData.label_tone : '');

    const cellsEl = rowEl.lastChild;
    const cells = rowData.cells || [];
    while (cellsEl.children.length > cells.length) {
      cellsEl.removeChild(cellsEl.lastChild);
    }
    cells.forEach((cell, idx) => {
      let inp = cellsEl.children[idx];
      const cellKey = `${rowData.h}:${cell.i}`;
      if (!inp) {
        inp = document.createElement('input');
        inp.type = 'text';
        inp.className = 'cell-input';
        inp.dataset.cellKey = cellKey;
        inp.addEventListener('keydown', async (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            const res = await postAction({type: 'commit', h: rowData.h, i: cell.i, text: inp.value, enter: true});
            if (!res || res.ok !== false) inp.blur();
          }
        });
        inp.addEventListener('blur', () => {
          if (inp.disabled) return;
          postAction({type: 'commit', h: rowData.h, i: cell.i, text: inp.value, enter: false});
        });
        cellsEl.appendChild(inp);
      }
      inp.dataset.cellKey = cellKey;
      inp.disabled = !!cell.disabled;
      inp.classList.toggle('wait', !!cell.wait);
      if (activeKey !== cellKey && inp.value !== (cell.text || '')) {
        inp.value = cell.text || '';
      }
    });
  });

  if (typeof s.scroll_seq === 'number' && s.scroll_seq !== pendingScrollSeq) {
    pendingScrollSeq = s.scroll_seq;
    const targetRow = list.querySelector(`.row[data-row="${s.scroll_row}"]`);
    if (targetRow) {
      targetRow.scrollIntoView({block: 'center', behavior: 'auto'});
    }
    if (s.focus_cell) {
      const targetInp = list.querySelector(`input[data-cell-key="${s.focus_cell}"]`);
      if (targetInp && !targetInp.disabled) {
        setTimeout(() => targetInp.focus(), 30);
      }
    }
  }
}

async function fetchState() {
  try {
    const res = await fetch('/api/state?token=' + encodeURIComponent(TOKEN));
    const j = await res.json();
    if (j && j.ok) render(j.state);
  } catch (_) {}
}

$('btnSettings').onclick = () => postAction({type: 'open_settings'});

let es = null;
function connectSSE() {
  if (es) { try { es.close(); } catch (_) {} }
  es = new EventSource('/api/events?token=' + encodeURIComponent(TOKEN));
  es.addEventListener('update', () => fetchState());
  es.onerror = () => {
    try { es.close(); } catch (_) {}
    setTimeout(connectSSE, 1500);
  };
}

fetchState();
connectSSE();
</script>
</body>
</html>
"""
