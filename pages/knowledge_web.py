# -*- coding: utf-8 -*-
"""📚 知识库本地 WebView2 前端（与沟通页同款纯本地单页风格，支持离线运行）。"""

_KNOWLEDGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OIT 知识库</title>
<style>
  :root {
    --bg: #0f131b;
    --bg-card: #171c28;
    --border: #2a3346;
    --text: #e6edf7;
    --text-dim: #8b97ad;
    --accent: #67d5f7;
    --accent-hover: #8be0fa;
    --accent-fill: #2563eb;
    --on-accent-fill: #ffffff;
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
    padding: 16px 24px 14px;
    gap: 8px;
  }
  .hdr {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
  }
  .hdr-title { font-size: 20px; font-weight: 700; letter-spacing: 0.3px; }
  .mode-group {
    display: flex; align-items: center; gap: 12px; font-size: 12.5px;
  }
  .mode-group label {
    display: flex; align-items: center; gap: 5px; cursor: pointer;
  }
  .toolbar {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px;
  }
  .btn {
    appearance: none; border: 1px solid var(--border);
    background: var(--bg-card); color: var(--text);
    padding: 5px 10px; border-radius: 6px;
    font-size: 12.5px; cursor: pointer; font-family: inherit;
    transition: background .12s, border-color .12s, transform .08s;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .btn:hover:not(:disabled) { border-color: var(--accent); background: rgba(127,127,127,0.12); }
  .btn:active:not(:disabled) { transform: translateY(1px); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn-accent {
    background: var(--accent-fill); color: var(--on-accent-fill);
    border-color: var(--accent-fill); font-weight: 600;
  }
  .btn-accent:hover:not(:disabled) { filter: brightness(1.08); }
  .filters {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  }
  .select, .search-input {
    background: var(--bg-card); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 9px; font-size: 12.5px; font-family: inherit;
    outline: none;
  }
  .select { cursor: pointer; }
  .select:focus, .search-input:focus { border-color: var(--accent); }
  .search-input { flex: 1; min-width: 140px; user-select: text; }
  .tabs {
    display: flex; gap: 4px; border-bottom: 1px solid var(--border);
  }
  .tab {
    background: transparent; color: var(--text-dim);
    border: 1px solid transparent; border-bottom: none;
    border-radius: 6px 6px 0 0;
    padding: 6px 13px; font-size: 13px; cursor: pointer; font-family: inherit;
    transition: color .12s, background .12s;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    background: var(--bg-card); color: var(--accent);
    border-color: var(--border); font-weight: 600;
  }
  .tab-note {
    font-size: 12px; color: var(--text-dim); line-height: 1.45;
    padding: 2px 2px;
  }
  .list-wrap {
    flex: 1; min-height: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--bg-card);
    overflow-y: auto;
    display: flex; flex-direction: column;
  }
  .item {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid rgba(127,127,127,0.12);
    cursor: pointer;
    transition: background .1s;
  }
  .item:last-child { border-bottom: none; }
  .item:hover { background: rgba(127,127,127,0.1); }
  .item.selected { background: rgba(103,213,247,0.16); }
  .item-left {
    display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1;
  }
  .item-chk { cursor: pointer; flex-shrink: 0; }
  .item-title {
    white-space: pre; overflow: hidden; text-overflow: ellipsis;
    font-size: 13px; color: var(--text);
  }
  .item-info {
    font-size: 12px; color: var(--text-dim); white-space: nowrap; flex-shrink: 0;
  }
  .empty {
    padding: 36px 16px; text-align: center; color: var(--text-dim); font-size: 13px;
  }
  .footer {
    display: flex; flex-direction: column; gap: 6px;
  }
  .footer-row {
    display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  }
  .hidden { display: none !important; }
  .list-wrap::-webkit-scrollbar { width: 8px; }
  .list-wrap::-webkit-scrollbar-track { background: transparent; }
  .list-wrap::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
</style>
</head>
<body>
<div class="app">
  <div class="hdr">
    <div id="title" class="hdr-title">📚 知识库</div>
    <div class="mode-group">
      <label><input type="radio" name="mode" value="exploration" id="modeExp" /> <span id="lblExp">探索模式</span></label>
      <label><input type="radio" name="mode" value="conservative" id="modeCon" /> <span id="lblCon">保守模式</span></label>
    </div>
  </div>

  <div class="toolbar">
    <button id="btnObsidian" class="btn"></button>
    <button id="btnNew" class="btn"></button>
    <button id="btnImport" class="btn"></button>
    <button id="btnRefresh" class="btn"></button>
    <button id="btnHelp" class="btn"></button>
    <button id="btnSettings" class="btn"></button>
  </div>

  <div class="filters">
    <select id="selSubject" class="select"></select>
    <select id="selCategory" class="select"></select>
    <input id="inpQuery" class="search-input" type="text" placeholder="搜索..." />
    <button id="btnSearch" class="btn"></button>
    <button id="btnClear" class="btn"></button>
    <select id="selSort" class="select"></select>
  </div>

  <div id="tabs" class="tabs"></div>
  <div id="tabNote" class="tab-note hidden"></div>
  <div id="listWrap" class="list-wrap"></div>

  <div class="footer">
    <div class="footer-row">
      <button id="btnSelectAll" class="btn"></button>
      <button id="btnClearSel" class="btn"></button>
      <button id="btnBatchApprove" class="btn btn-accent hidden"></button>
      <button id="btnBatchDelete" class="btn"></button>
    </div>
    <div class="footer-row">
      <button id="btnOpen" class="btn"></button>
      <button id="btnDetails" class="btn"></button>
    </div>
  </div>
</div>
<script>
const TOKEN = __TOKEN__;
const $ = id => document.getElementById(id);

let curState = null;
let activeTab = 'tactical';
let selectedIds = new Set();
let lastClickedIdx = -1;
let queryTimer = null;

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
  r.setProperty('--accent-fill', c.accent_fill || c.accent);
  r.setProperty('--on-accent-fill', c.on_accent_fill || '#ffffff');
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

function setOptions(sel, values, current) {
  const same = sel.options.length === values.length &&
    values.every((v, i) => sel.options[i].value === v);
  if (!same) {
    sel.innerHTML = '';
    values.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      sel.appendChild(opt);
    });
  }
  if (sel.value !== current) sel.value = current;
}

function renderList() {
  if (!curState) return;
  const items = (curState.items && curState.items[activeTab]) || [];
  const validIds = new Set(items.map(x => x.id));
  selectedIds = new Set([...selectedIds].filter(id => validIds.has(id)));

  const note = (curState.tab_notes && curState.tab_notes[activeTab]) || '';
  const noteEl = $('tabNote');
  if (note) {
    noteEl.textContent = note;
    noteEl.classList.remove('hidden');
  } else {
    noteEl.classList.add('hidden');
  }

  const isProposals = activeTab === 'proposals';
  $('btnBatchApprove').classList.toggle('hidden', !isProposals);
  const L = curState.labels || {};
  $('btnBatchDelete').textContent = isProposals ? (L.batch_reject || '批量拒绝') : (L.batch_delete || '批量删除');

  const wrap = $('listWrap');
  wrap.innerHTML = '';
  if (!items.length) {
    const emp = document.createElement('div');
    emp.className = 'empty';
    emp.textContent = L.empty || '暂无条目';
    wrap.appendChild(emp);
    return;
  }
  items.forEach((it, idx) => {
    const row = document.createElement('div');
    row.className = 'item' + (selectedIds.has(it.id) ? ' selected' : '');
    const left = document.createElement('div');
    left.className = 'item-left';
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.className = 'item-chk';
    chk.checked = selectedIds.has(it.id);
    chk.onclick = (e) => {
      e.stopPropagation();
      if (chk.checked) selectedIds.add(it.id);
      else selectedIds.delete(it.id);
      lastClickedIdx = idx;
      row.classList.toggle('selected', chk.checked);
    };
    const t = document.createElement('div');
    t.className = 'item-title';
    t.textContent = it.title || '';
    left.appendChild(chk);
    left.appendChild(t);
    const info = document.createElement('div');
    info.className = 'item-info';
    info.textContent = it.info || '';
    row.appendChild(left);
    row.appendChild(info);

    row.onclick = (e) => {
      if (e.shiftKey && lastClickedIdx >= 0) {
        const a = Math.min(lastClickedIdx, idx);
        const b = Math.max(lastClickedIdx, idx);
        for (let k = a; k <= b; k++) selectedIds.add(items[k].id);
      } else if (e.ctrlKey || e.metaKey) {
        if (selectedIds.has(it.id)) selectedIds.delete(it.id);
        else selectedIds.add(it.id);
      } else {
        selectedIds = new Set([it.id]);
      }
      lastClickedIdx = idx;
      renderList();
    };
    row.ondblclick = () => {
      selectedIds = new Set([it.id]);
      postAction({type: 'open_item', kind: activeTab, id: it.id});
    };
    wrap.appendChild(row);
  });
}

function render(s) {
  if (!s) return;
  curState = s;
  applyColors(s.colors);
  const L = s.labels || {};
  $('title').textContent = L.title || '📚 知识库';
  $('lblExp').textContent = L.exploration || '探索模式';
  $('lblCon').textContent = L.conservative || '保守模式';
  $('modeExp').checked = s.mode !== 'conservative';
  $('modeCon').checked = s.mode === 'conservative';

  $('btnObsidian').textContent = L.open_obsidian || '在 Obsidian 中打开';
  $('btnNew').textContent = L.new_card || '新建卡片';
  $('btnImport').textContent = L.import_notes || '导入笔记';
  $('btnRefresh').textContent = L.refresh || '刷新索引';
  $('btnHelp').textContent = L.help || '知识库帮助';
  $('btnSettings').textContent = L.settings || '知识库设置';
  $('btnSearch').textContent = L.search || '搜索';
  $('btnClear').textContent = L.clear || '清空';
  $('btnSelectAll').textContent = L.select_all || '全选';
  $('btnClearSel').textContent = L.clear_sel || '全不选';
  $('btnBatchApprove').textContent = L.batch_approve || '批量批准';
  $('btnOpen').textContent = L.open_selected || '查看所选卡片或提案';
  $('btnDetails').textContent = L.manage_selected || '管理所选卡片';

  setOptions($('selSubject'), s.subjects || [], s.subject || '');
  setOptions($('selCategory'), s.categories || [], s.category || '');
  setOptions($('selSort'), s.sorts || [], s.sort || '');
  if (document.activeElement !== $('inpQuery') && $('inpQuery').value !== (s.query || '')) {
    $('inpQuery').value = s.query || '';
  }

  if (s.active_tab && s.active_tab !== activeTab) {
    activeTab = s.active_tab;
    selectedIds.clear();
  }

  const tabsEl = $('tabs');
  tabsEl.innerHTML = '';
  (s.tabs || []).forEach(tab => {
    const b = document.createElement('button');
    b.className = 'tab' + (tab.key === activeTab ? ' active' : '');
    b.textContent = tab.label;
    b.onclick = () => {
      if (activeTab !== tab.key) {
        activeTab = tab.key;
        selectedIds.clear();
        lastClickedIdx = -1;
        postAction({type: 'set_tab', tab: tab.key});
        renderList();
        render(curState);
      }
    };
    tabsEl.appendChild(b);
  });

  renderList();
}

async function fetchState() {
  try {
    const res = await fetch('/api/state?token=' + encodeURIComponent(TOKEN));
    const j = await res.json();
    if (j && j.ok) render(j.state);
  } catch (_) {}
}

$('modeExp').onchange = () => postAction({type: 'set_mode', mode: 'exploration'});
$('modeCon').onchange = () => postAction({type: 'set_mode', mode: 'conservative'});
$('btnObsidian').onclick = () => postAction({type: 'open_obsidian'});
$('btnNew').onclick = () => postAction({type: 'new_card'});
$('btnImport').onclick = () => postAction({type: 'import_notes'});
$('btnRefresh').onclick = () => postAction({type: 'refresh'});
$('btnHelp').onclick = () => postAction({type: 'help'});
$('btnSettings').onclick = () => postAction({type: 'settings'});

$('selSubject').onchange = () => postAction({type: 'set_subject', value: $('selSubject').value});
$('selCategory').onchange = () => postAction({type: 'set_category', value: $('selCategory').value});
$('selSort').onchange = () => postAction({type: 'set_sort', value: $('selSort').value});
$('btnSearch').onclick = () => postAction({type: 'set_query', value: $('inpQuery').value});
$('btnClear').onclick = () => {
  $('inpQuery').value = '';
  postAction({type: 'set_query', value: ''});
};
$('inpQuery').addEventListener('input', () => {
  clearTimeout(queryTimer);
  queryTimer = setTimeout(() => postAction({type: 'set_query', value: $('inpQuery').value}), 150);
});
$('inpQuery').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    clearTimeout(queryTimer);
    postAction({type: 'set_query', value: $('inpQuery').value});
  } else if (e.key === 'Escape') {
    $('inpQuery').value = '';
    postAction({type: 'set_query', value: ''});
  }
});

$('btnSelectAll').onclick = () => {
  const items = (curState && curState.items && curState.items[activeTab]) || [];
  selectedIds = new Set(items.map(x => x.id));
  renderList();
};
$('btnClearSel').onclick = () => {
  selectedIds.clear();
  renderList();
};
$('btnBatchApprove').onclick = () => {
  if (!selectedIds.size) return;
  postAction({type: 'approve_selected', ids: [...selectedIds]});
};
$('btnBatchDelete').onclick = () => {
  if (!selectedIds.size) return;
  postAction({type: 'delete_selected', kind: activeTab, ids: [...selectedIds]});
};
$('btnOpen').onclick = () => {
  const first = [...selectedIds][0];
  if (first) postAction({type: 'open_item', kind: activeTab, id: first});
};
$('btnDetails').onclick = () => {
  const first = [...selectedIds][0];
  if (first) postAction({type: 'details_item', kind: activeTab, id: first});
};

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
    e.preventDefault();
    $('inpQuery').focus();
    $('inpQuery').select();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && document.activeElement !== $('inpQuery')) {
    e.preventDefault();
    $('btnSelectAll').click();
  } else if (e.key === 'Delete' && document.activeElement !== $('inpQuery') && selectedIds.size) {
    $('btnBatchDelete').click();
  }
});

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
