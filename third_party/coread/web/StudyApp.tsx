
import React, { useState, useEffect, useCallback, useRef, startTransition, useLayoutEffect, useMemo } from 'react';
import { api, BASE, COREAD_ACCESS_KEY, ROOM_OWNER_KEY } from './api';
import { useReadingClock, flushReading } from './useReadingClock';
import ReadingJournal, { duration } from './ReadingJournal';
// BackupControls removed — inline backup controls from home version
import './vendor-hyalite.js';
import { measurePageLines, focusRange, stepFocusIndex, type LineRect, type FocusUnit, type FocusBlock } from './lineFocus';
import { readerColumns, type PageLayout } from './readerSpread';
import { createSegmenter, splitAssistTokens, placeTokens, tokenStyle, assistActive, assistSpacing, type AssistToken, type AssistOpts, type SpacingLevel } from './assistRead';

const INK = 'hsl(40, 8%, 16%)';
const INK2 = 'hsl(40, 5%, 48%)';
// 独自阅读沿用同一书库, 不启动人设会话或锁定 OIT 沟通页.
const READ_TOGETHER = new URL(window.location.href).searchParams.get('reading_mode') !== 'solo';
const COREAD_PERSONA_NAME = READ_TOGETHER ? (new URL(window.location.href).searchParams.get('persona_name') || 'AI') : '';

const COREAD_POP_CSS = `
.coread-study {
    --spring-pop: linear(0, 0.0086 2.8%, 0.032 5.6%, 0.0666 8.3%, 0.1095 11.1%, 0.1584 13.9%, 0.2111 16.7%, 0.266 19.4%, 0.3217 22.2%, 0.3772 25%, 0.4315 27.8%, 0.484 30.6%, 0.5343 33.3%, 0.5818 36.1%, 0.6265 38.9%, 0.6681 41.7%, 0.7066 44.4%, 0.742 47.2%, 0.7743 50%, 0.8037 52.8%, 0.8303 55.6%, 0.8542 58.3%, 0.8756 61.1%, 0.8946 63.9%, 0.9114 66.7%, 0.9262 69.4%, 0.9392 72.2%, 0.9504 75%, 0.9602 77.8%, 0.9686 80.6%, 0.9758 83.3%, 0.9819 86.1%, 0.9871 88.9%, 0.9913 91.7%, 0.9949 94.4%, 0.9978 97.2%, 1);
    --ease-drawer: cubic-bezier(0.32, 0.72, 0, 1);
    --ease-out-strong: cubic-bezier(0.23, 1, 0.32, 1);
}
@keyframes crFade { from { opacity: 0; } to { opacity: 1; } }
@keyframes crMenuPop { from { transform: scale(0.90); } to { transform: scale(1); } }
@keyframes crMenuClose { from { transform: scale(1); opacity: 1; } to { transform: scale(0.95); opacity: 0; } }
.coread-study .cr-rmenu-anim { animation: crMenuPop 310ms var(--spring-pop) both, crFade 110ms var(--ease-out-strong) both; }
.coread-study .cr-rmenu-closing { animation: crMenuClose 170ms var(--ease-out-strong) both; }
@keyframes crSheetIn { from { transform: translateY(104%); } to { transform: translateY(0); } }
@keyframes crSheetOut { from { transform: translateY(0); } to { transform: translateY(104%); } }
.coread-study .cr-sheet-in { animation: crSheetIn 320ms var(--ease-drawer) both; }
.coread-study .cr-sheet-out { animation: crSheetOut 220ms var(--ease-out-strong) both; }
.coread-study .cr-scrim-in { animation: crFade 200ms ease-out both; }
.coread-study .cr-scrim-out { animation: crFade 180ms ease-out both reverse; }
@keyframes crRiseIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
@keyframes crItemIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }
.coread-study .cr-rise-in { animation: crRiseIn 190ms var(--ease-out-strong) both; }
.coread-study .cr-item-in { animation: crItemIn 190ms var(--ease-out-strong) both; }
@supports not (animation-timing-function: linear(0, 1)) {
    .coread-study .cr-rmenu-anim { animation: crMenuPop 220ms cubic-bezier(0.23, 1, 0.32, 1) both, crFade 110ms cubic-bezier(0.23, 1, 0.32, 1) both; }
}
@keyframes crPopScale { from { transform: scale(0.90); border-radius: 24px; } to { transform: scale(1); border-radius: 18px; } }
@keyframes crPopClose { from { transform: scale(1); opacity: 1; border-radius: 18px; } to { transform: scale(0.95); opacity: 0; border-radius: 22px; } }
@keyframes crDotsOut { from { opacity: 1; transform: scale(1); } to { opacity: 0; transform: scale(0.7); } }
@keyframes crDotsIn { from { opacity: 0; transform: scale(0.7); } to { opacity: 1; transform: scale(1); } }
.coread-study .cr-pop-anim { animation: crPopScale 310ms var(--spring-pop) both, crFade 110ms var(--ease-out-strong) both; }
.coread-study .cr-pop-closing { animation: crPopClose 170ms var(--ease-out-strong) both; }
.coread-study .cr-dots-out { animation: crDotsOut 160ms var(--ease-out-strong) both; }
.coread-study .cr-dots-in { animation: crDotsIn 160ms 80ms var(--ease-out-strong) both; }
@supports not (animation-timing-function: linear(0, 1)) {
    .coread-study .cr-pop-anim { animation: crPopScale 220ms cubic-bezier(0.23, 1, 0.32, 1) both, crFade 110ms cubic-bezier(0.23, 1, 0.32, 1) both; }
}
.coread-study .cr-views { display: grid; grid-template-columns: minmax(0, 1fr); }
.coread-study .cr-view { grid-area: 1/1; min-width: 0; }
.coread-study .cr-view-detail { opacity: 0; visibility: hidden; }
.coread-study .cr-show-detail .cr-view-list { opacity: 0; visibility: hidden; }
.coread-study .cr-show-detail .cr-view-detail { opacity: 1; visibility: visible; }
@keyframes crShelfIn { from { opacity: 0; transform: translateY(8px) scale(0.97); } to { opacity: 1; transform: none; } }
.coread-study .cr-shelf-in { animation: crShelfIn 260ms var(--ease-out-strong) both; }
@keyframes crPopSmC { from { opacity: 0; transform: translateX(-50%) scale(0.92); } to { opacity: 1; transform: translateX(-50%) scale(1); } }
.coread-study .cr-pop-sm-c { animation: crPopSmC 170ms var(--ease-out-strong) both; }
.coread-study .cr-pw-in { animation: crFade 300ms ease-out both; }
.coread-study .cr-heat-pop { animation: crFade 380ms var(--ease-out-strong) both; }
@media (prefers-reduced-motion: reduce) {
    .coread-study .cr-rmenu-anim { animation: crFade 150ms ease-out both; }
    .coread-study .cr-rmenu-closing { animation: crFade 150ms ease-out both reverse; }
    .coread-study .cr-sheet-in { animation: crFade 150ms ease-out both; }
    .coread-study .cr-sheet-out { animation: crFade 150ms ease-out both reverse; }
    .coread-study .cr-pop-anim { animation: crFade 150ms ease-out both; }
    .coread-study .cr-pop-closing { animation: crFade 150ms ease-out both reverse; }
    .coread-study .cr-rise-in, .coread-study .cr-pop-sm-c, .coread-study .cr-shelf-in { animation: crFade 150ms ease-out both; }
}
`;

function themeColors(h: number, s: number, l: number) {
    const primary = `hsl(${h}, ${s}%, ${l}%)`;
    const primaryLight = `hsl(${h}, ${s}%, 92%)`;
    const primaryBg = `hsl(${h}, ${Math.max(s - 5, 0)}%, 96%)`;
    const primaryBorder = `hsla(${h}, ${s}%, ${l}%, 0.18)`;
    const primaryDark = `hsl(${h}, ${s}%, ${Math.max(l - 18, 20)}%)`;
    const warmAccent = `hsl(${(h + 30) % 360}, ${Math.min(s + 10, 80)}%, 72%)`;
    const warmBg = `hsl(${(h + 30) % 360}, ${Math.min(s + 10, 80)}%, 94%)`;
    const grad1 = `hsl(${h}, ${Math.max(s - 5, 0)}%, 94%)`;
    const grad2 = `hsl(${(h + 20) % 360}, ${Math.max(s - 8, 0)}%, 92%)`;
    const grad3 = `hsl(${(h + 40) % 360}, ${Math.max(s - 12, 0)}%, 95%)`;
    const shenColor = `hsl(${h}, ${Math.min(s + 5, 60)}%, ${Math.max(l - 5, 35)}%)`;
    const shenBg = `hsl(${h}, ${Math.min(s + 5, 60)}%, 93%)`;
    const tongColor = `hsl(${(h + 150) % 360}, 45%, 55%)`;
    const tongBg = `hsl(${(h + 150) % 360}, 35%, 93%)`;
    const shenHL = `hsla(${h}, ${Math.min(s + 10, 55)}%, 82%, 0.5)`;
    const tongHL = `hsla(340, 50%, 82%, 0.5)`;
    return { primary, primaryLight, primaryBg, primaryBorder, primaryDark, warmAccent, warmBg, grad1, grad2, grad3, shenColor, shenBg, tongColor, tongBg, shenHL, tongHL };
}

interface Book {
    id: number;
    title: string;
    total_paragraphs: number;
    created_at: string;
    current_page: number | null;
    current_display_page?: number | null;
    current_paragraph_idx?: number | null;
    current_paragraph_offset?: number | null;
    total_pages?: number;
    pagination_source?: 'browser' | 'fallback';
    comment_count: number;
    cover_image?: string | null;
    last_read_at?: string | null;
    last_opened_at?: string | null;
    finished_at?: string | null;
}
interface Paragraph { idx: number; content: string; }
interface Comment { id: number; book_id: number; paragraph_idx: number; sel_end_para_idx: number | null; sel_start_idx: number | null; sel_end_idx: number | null; selected_text: string | null; from_who: string; content: string; created_at: string; reply_to: number | null; }
interface ReadingStats {
    today: string;
    today_seconds: number;
    total_seconds: number;
    currentStreak: number;
    longestStreak: number;
    readingDays: number;
    daily: { reading_date: string; seconds: number }[];
    books: { id: number; title: string; total_seconds: number; finished_at: string | null; last_read_at: string | null }[];
    notes: { id: number; book_id: number | null; book_title: string | null; reading_date: string | null; from_who: string; content: string; created_at: string }[];
}
interface PageBreak { paraIndex: number; offset: number; }
interface PageFragment extends Paragraph { sourceIdx: number; startOffset: number; endOffset: number; isPartialStart: boolean; isPartialEnd: boolean; }
interface ReplyNotice {
    id: number;
    paragraph_idx: number;
    content: string;
    from_who?: string;
    created_at?: string;
    reply_to?: number | null;
    parent_id?: number | null;
    parent_from?: string;
    parent_content?: string;
    sel_start_idx?: number | null;
    sel_end_idx?: number | null;
    selected_text?: string | null;
    parent_paragraph_idx?: number | null;
    parent_sel_start_idx?: number | null;
    parent_sel_end_idx?: number | null;
    parent_selected_text?: string | null;
}

const BOOK_COVERS = [
    'linear-gradient(145deg, rgba(204,209,231,0.86), rgba(244,246,250,0.76))',
    'linear-gradient(145deg, rgba(231,201,213,0.86), rgba(250,244,247,0.76))',
    'linear-gradient(145deg, rgba(199,221,225,0.86), rgba(246,250,250,0.76))',
    'linear-gradient(145deg, rgba(214,225,207,0.86), rgba(248,250,245,0.76))',
    'linear-gradient(145deg, rgba(232,216,192,0.86), rgba(251,248,242,0.76))',
    'linear-gradient(145deg, rgba(212,203,230,0.86), rgba(248,246,251,0.76))',
];

const STUDY_THEME_CSS = `
.xiaowo-study {
    color: #41394f;
}
.xiaowo-study button {
    transition: transform 160ms ease, background 160ms ease, border-color 160ms ease;
}
.xiaowo-study button:active {
    transform: scale(0.98);
}
`;

const READER_PAGE_PADDING = '56px 28px calc(24px + env(safe-area-inset-bottom))';
const READER_VERTICAL_PADDING_STATIC = 88;
function getSafeAreaBottom(): number {
    if (typeof document === 'undefined') return 0;
    const probe = document.createElement('div');
    probe.style.cssText = 'position:fixed;bottom:0;left:0;width:0;padding-bottom:env(safe-area-inset-bottom);visibility:hidden;pointer-events:none;';
    document.body.appendChild(probe);
    const h = probe.offsetHeight;
    document.body.removeChild(probe);
    return h;
}
const READER_HORIZONTAL_PADDING = 56;
// 平板和电脑上每页一行最多这么多个汉字宽（单页双页都算）：宽屏一行铺满七八十字，眼睛回行容易跳错
const READER_MAX_LINE_CHARS = 36;
const SPREAD_GUTTER = 72;
// 双页：外侧边距 = READER_HORIZONTAL_PADDING，两栏正文之间 = SPREAD_GUTTER，跟 readerColumns 的算法对上
const SPREAD_LEFT_PADDING = `56px ${SPREAD_GUTTER / 2}px calc(24px + env(safe-area-inset-bottom)) ${READER_HORIZONTAL_PADDING}px`;
const SPREAD_RIGHT_PADDING = `56px ${READER_HORIZONTAL_PADDING}px calc(24px + env(safe-area-inset-bottom)) ${SPREAD_GUTTER / 2}px`;
function decodeEntities(s: string): string {
    return s.replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCharCode(parseInt(h, 16)));
}
const PARA_GAP = 18;
const CHAPTER_GAP_TOP = 40;
const CHAPTER_GAP_BOTTOM = 28;

function localDateString(date = new Date()): string {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}
function localDay(time: number): string {
    const d = new Date(time);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function fmtDuration(seconds: number): string {
    const s = Math.floor(seconds || 0);
    return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m` : s >= 60 ? `${Math.floor(s / 60)} 分钟` : `${s} 秒`;
}
function fmtClock(seconds: number): string {
    const s = Math.floor(seconds || 0);
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}分${sec}秒` : `${sec}秒`;
}

function formatReadingTime(seconds: number): string {
    const minutes = Math.floor(Number(seconds || 0) / 60);
    if (minutes < 1) return '不足 1 分钟';
    if (minutes < 60) return `${minutes} 分钟`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

// 大书（对齐SullyOS共读室 v2.2.24）：不做窗口化——全局连续视觉分页。
// 超阈值的书首开走渐进分页：分块测量（块间让出主线程不卡UI）+ 完成后写分页缓存，之后秒开。
const PROGRESSIVE_MEASURE_THRESHOLD = 15000;
const PARA_FETCH_CHUNK = 10000;
const MEASURE_CHUNK = 500;
// 目录行高（窗口化渲染用，固定行高才能按滚动位置直接换算可视窗口）
const TOC_ROW_H = 44;
// 后手优化：缓存miss时先分当前位置±PROVISIONAL_WIN段立即可读，全书分页后台补全
const PROVISIONAL_WIN = 2500;

// 分页/段落缓存主存 IndexedDB：大书分页结果几百KB起，localStorage(5-10MB)写不下
// 或被清理→每次重开都重分页。localStorage 只作 IDB 不可用时的后手兜底。
const idbOpen = (): Promise<IDBDatabase | null> => new Promise((resolve) => {
    try {
        const req = indexedDB.open(`study-reader-cache-${localStorage.getItem('coread-cache-generation') || 'original'}`, 2);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains('pagebreaks')) db.createObjectStore('pagebreaks');
            if (!db.objectStoreNames.contains('paragraphs')) db.createObjectStore('paragraphs');
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
    } catch { resolve(null); }
});
const idbGet = (key: string): Promise<string | null> =>
    idbOpen().then(db => new Promise<string | null>((resolve) => {
        if (!db) return resolve(null);
        try {
            const req = db.transaction('pagebreaks', 'readonly').objectStore('pagebreaks').get(key);
            req.onsuccess = () => resolve(typeof req.result === 'string' ? req.result : null);
            req.onerror = () => resolve(null);
        } catch { resolve(null); }
    }));
const idbSet = (key: string, value: string): Promise<boolean> =>
    idbOpen().then(db => new Promise<boolean>((resolve) => {
        if (!db) return resolve(false);
        try {
            const tx = db.transaction('pagebreaks', 'readwrite');
            tx.objectStore('pagebreaks').put(value, key);
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => resolve(false);
            tx.onabort = () => resolve(false);
        } catch { resolve(false); }
    }));
const idbDel = (key: string): Promise<void> =>
    idbOpen().then(db => new Promise<void>((resolve) => {
        if (!db) return resolve();
        try {
            const tx = db.transaction('pagebreaks', 'readwrite');
            tx.objectStore('pagebreaks').delete(key);
            tx.oncomplete = () => resolve();
            tx.onerror = () => resolve();
        } catch { resolve(); }
    }));
const idbGetParas = (key: string): Promise<string | null> =>
    idbOpen().then(db => new Promise<string | null>((resolve) => {
        if (!db) return resolve(null);
        try {
            const req = db.transaction('paragraphs', 'readonly').objectStore('paragraphs').get(key);
            req.onsuccess = () => resolve(typeof req.result === 'string' ? req.result : null);
            req.onerror = () => resolve(null);
        } catch { resolve(null); }
    }));
const cacheComments = (bookId: number, list: Comment[]) => {
    idbSetParas(`comments-v1-${bookId}`, JSON.stringify(list)).catch(() => {});
};
const idbSetParas = (key: string, value: string): Promise<boolean> =>
    idbOpen().then(db => new Promise<boolean>((resolve) => {
        if (!db) return resolve(false);
        try {
            const tx = db.transaction('paragraphs', 'readwrite');
            tx.objectStore('paragraphs').put(value, key);
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => resolve(false);
            tx.onabort = () => resolve(false);
        } catch { resolve(false); }
    }));
const idbDelParas = (key: string): Promise<void> =>
    idbOpen().then(db => new Promise<void>((resolve) => {
        if (!db) return resolve();
        try {
            const tx = db.transaction('paragraphs', 'readwrite');
            tx.objectStore('paragraphs').delete(key);
            tx.oncomplete = () => resolve();
            tx.onerror = () => resolve();
        } catch { resolve(); }
    }));

function toast(msg: string) {
    const el = document.createElement('div');
    el.textContent = msg;
    Object.assign(el.style, { position: 'fixed', bottom: '80px', left: '50%', transform: 'translateX(-50%)', background: 'rgba(0,0,0,0.75)', color: '#fff', padding: '8px 20px', borderRadius: '20px', fontSize: '13px', zIndex: '9999', pointerEvents: 'none', transition: 'opacity 0.3s' });
    document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 2000);
}

// 阅读页共用开关：≡ 菜单阅读钟、辅助阅读抽屉同款
const ReaderSwitch: React.FC<{ on: boolean; night: boolean }> = ({ on, night }) => (
    <span style={{
        width: 40, height: 24, borderRadius: 12, position: 'relative', flexShrink: 0,
        background: on ? (night ? 'rgba(221,221,221,0.85)' : INK) : (night ? 'rgba(255,255,255,0.16)' : 'rgba(60,55,45,0.14)'),
        transition: 'background 180ms cubic-bezier(0.23,1,0.32,1)',
    }}>
        <span style={{
            position: 'absolute', top: 3, left: 3, width: 18, height: 18, borderRadius: '50%',
            background: on && night ? '#333' : night ? '#ddd' : '#fff',
            boxShadow: '0 1px 4px rgba(40,35,25,0.25)',
            transform: on ? 'translateX(16px)' : 'translateX(0)',
            transition: 'transform 180ms cubic-bezier(0.23,1,0.32,1)',
        }} />
    </span>
);

// 阅读页共用胶囊分段：白 thumb 回弹、选中侧加粗（阅读设置「翻页」、辅助阅读「间距」）
function ReaderSegPill<T extends string | number | boolean>({ options, value, onChange, night }: { options: [string, T][]; value: T; onChange: (v: T) => void; night: boolean }) {
    const n = options.length;
    const idx = Math.max(0, options.findIndex(([, v]) => v === value));
    const ink = night ? '#ddd' : INK, ink2 = night ? '#888' : INK2;
    return (
        <div style={{ position: 'relative', flex: 1, height: 34, borderRadius: 999, background: night ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.08)', display: 'flex' }}>
            <div style={{
                position: 'absolute', top: 3, bottom: 3, width: `calc(${100 / n}% - ${6 / n}px)`,
                left: `calc(${(100 / n) * idx}% + ${3 - (6 / n) * idx}px)`,
                borderRadius: 999, background: night ? '#3a3a3a' : '#fff',
                boxShadow: '0 1px 6px rgba(40,35,25,0.22)',
                transition: 'left .42s cubic-bezier(0.34, 1.65, 0.5, 1)',
            }} />
            {options.map(([label, v]) => (
                <button key={label} onClick={() => onChange(v)}
                    style={{
                        flex: 1, position: 'relative', zIndex: 1, background: 'none', border: 'none', cursor: 'pointer',
                        fontSize: 13, color: value === v ? ink : ink2,
                        fontWeight: value === v ? 700 : 400,
                        transition: 'color .25s, font-weight .25s',
                    }}>{label}</button>
            ))}
        </div>
    );
}

// 阅读页底部玻璃抽屉：阅读设置、辅助阅读共用一份
const readerScrimStyle = (night: boolean): React.CSSProperties => ({ position: 'absolute', inset: 0, zIndex: 25, background: night ? 'rgba(0,0,0,0.5)' : 'rgba(30,26,20,0.35)' });
const readerSheetStyle = (night: boolean): React.CSSProperties => ({
    position: 'absolute', left: 8, right: 8, bottom: 8, zIndex: 26, borderRadius: 24,
    background: night ? 'rgba(38,38,38,0.65)' : 'linear-gradient(rgba(255,255,255,0.15), rgba(255,255,255,0) 40%), rgba(252,252,252,0.5)',
    backdropFilter: 'blur(40px) saturate(1.8)', WebkitBackdropFilter: 'blur(40px) saturate(1.8)' as any,
    boxShadow: night ? '0 -8px 40px rgba(0,0,0,0.5)' : '0 -8px 40px rgba(40,35,25,0.18), inset 0 1px 0 rgba(255,255,255,0.5)',
    border: `1px solid ${night ? 'rgba(255,255,255,0.07)' : 'rgba(60,55,45,0.08)'}`,
    padding: '16px 16px 18px',
});
const ReaderSheetHeader: React.FC<{ title: string; night: boolean; onClose: () => void; style?: React.CSSProperties }> = ({ title, night, onClose, style }) => (
    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14, ...style }}>
        <span style={{ fontSize: 15, fontWeight: 700, color: night ? '#ddd' : INK }}>{title}</span>
        <button onClick={onClose}
            style={{ marginLeft: 'auto', width: 26, height: 26, borderRadius: '50%', background: night ? 'rgba(255,255,255,0.10)' : 'rgba(60,55,45,0.08)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', color: night ? '#999' : INK2, cursor: 'pointer' }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
        </button>
    </div>
);
// 阅读尺框：和纸同色的实心块垫在字下面；细边和外阴影另起一层压在变浅遮罩上面，外阴影只画在框外，不盖字
const focusGlassStyle = (night: boolean): React.CSSProperties => ({
    borderRadius: 10, boxSizing: 'border-box', pointerEvents: 'none',
    background: night ? '#1a1a1a' : '#fffcf3',
    boxShadow: night
        ? 'inset 0 1px 0 rgba(255,255,255,0.07), inset 0 -1px 0 rgba(0,0,0,0.25)'
        : 'inset 0 1px 0 rgba(255,255,255,0.9), inset 0 -1px 0 rgba(60,50,30,0.04)',
});
const focusEdgeStyle = (night: boolean): React.CSSProperties => ({
    borderRadius: 10, boxSizing: 'border-box', pointerEvents: 'none', background: 'transparent',
    border: night ? '0.5px solid rgba(255,255,255,0.10)' : '0.5px solid rgba(60,50,30,0.08)',
    boxShadow: night
        ? '0 1px 2px rgba(0,0,0,0.45), 0 6px 18px rgba(0,0,0,0.40)'
        : '0 1px 2px rgba(60,50,30,0.07), 0 6px 16px rgba(60,50,30,0.08)',
});
const focusVeil = (night: boolean) => night ? 'rgba(26,26,26,0.62)' : 'rgba(255,252,243,0.62)';

const lsGet = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch {} };
const ASSIST_DEMO = '对于一种被认为已经过时的音乐格式，录音带的销售目前似乎正在快速推进。';
// 正文显示时会解码 HTML 实体，字数和原文对不上；这种段落辅助阅读跳过，免得切词偏移和批注偏移错位
const HAS_ENTITY = /&(?:quot|apos|lt|gt|amp|#\d+|#x[0-9a-fA-F]+);/;

const StudyApp: React.FC = () => {
    const c = themeColors(245, 25, 65);

    const [mode, setMode] = useState<'shelf' | 'reading'>('shelf');
    const [books, setBooks] = useState<Book[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [activeBook, setActiveBook] = useState<Book | null>(null);
    const [oitChatOpen, setOitChatOpen] = useState(false);
    const [oitChatMessages, setOitChatMessages] = useState<any[]>([]);
    const [oitChatInput, setOitChatInput] = useState('');
    const [oitChatStatus, setOitChatStatus] = useState('');
    const [oitChatBusy, setOitChatBusy] = useState(false);
    const oitChatEndRef = useRef<HTMLDivElement>(null);
    const oitChatSessionRef = useRef('');
    const [paragraphs, setParagraphs] = useState<Paragraph[]>([]);
    const [comments, setComments] = useState<Comment[]>([]);
    const [page, setPage] = useState(1);
    const [totalPages, setTotalPages] = useState(1);
    const [pageBreaks, setPageBreaks] = useState<PageBreak[]>([{ paraIndex: 0, offset: 0 }]);
    // 大书首开渐进分页进度（0~1），非 null 时 loading 显示百分比
    const [paginateProgress, setPaginateProgress] = useState<number | null>(null);
    const [pageFragments, setPageFragments] = useState<PageFragment[]>([]);
    const [pageFragmentsRight, setPageFragmentsRight] = useState<PageFragment[]>([]);
    const [readingLoading, setReadingLoading] = useState(false);
    const contentRef = useRef<HTMLDivElement>(null);
    const measureRef = useRef<HTMLDivElement>(null);
    const [allParas, setAllParas] = useState<Paragraph[]>([]);
    const [allComments, setAllComments] = useState<Comment[]>([]);
    const [pageHeight, setPageHeight] = useState(0);
    const [readerSize, setReaderSize] = useState({ width: 0, height: 0 });
    const savedParaIdxRef = useRef<number | null>(null);
    const savedParaOffsetRef = useRef(0);
    const currentParaIdxRef = useRef<number | null>(null);
    // 读到的位置（段号+段内字数）。只在翻页/跳转让它离开可见页时更新，转屏重排不改它，否则每转一次往前挪
    const anchorPosRef = useRef<{ idx: number; off: number } | null>(null);
    // 尺寸变了、新页表还没落地时，旧页表配新尺寸渲染出来的页不算翻过
    const repaginatingRef = useRef(false);
    // 后手优化：临时页表覆盖的段落区间（非null=全书分页仍在后台补全，窗外跳转先拦住）
    const provisionalRangeRef = useRef<{ from: number; to: number } | null>(null);

    const [commentingIdx, setCommentingIdx] = useState<number | null>(null);
    const [commentText, setCommentText] = useState('');
    const [selectedText, setSelectedText] = useState('');
    const [selRange, setSelRange] = useState<{ startPara: number; endPara: number; start: number; end: number } | null>(null);
    const [activeComments, setActiveComments] = useState<Comment[]>([]);
    const [replyingTo, setReplyingTo] = useState<Comment | null>(null);
    const [newReplies, setNewReplies] = useState<ReplyNotice[]>([]);
    const [showReplies, setShowReplies] = useState(false);
    const [returnPoint, setReturnPoint] = useState<{ page: number; paraIdx: number | null } | null>(null);
    const [floatingBar, setFloatingBar] = useState<{ startPara: number; endPara: number; text: string; start: number; end: number } | null>(null);

    const [showToc, setShowToc] = useState(false);
    const [tocPhase, setTocPhase] = useState<'closed' | 'open' | 'closing'>('closed');
    const [tocChapters, setTocChapters] = useState<{ idx: number; page: number; title: string }[]>([]);
    const tocListRef = useRef<HTMLDivElement>(null);
    // 目录窗口化：滚动位置与视口高（只渲染可视区±缓冲，几千章不全量挂DOM）
    const [tocScrollTop, setTocScrollTop] = useState(0);
    const [tocViewH, setTocViewH] = useState(0);
    const commentsRef = useRef<Comment[]>([]);
    const allCommentsRef = useRef<Comment[]>([]);
    const commentSubmittingRef = useRef(false);
    const suppressPageJumpRef = useRef(false);
    const replyPageRef = useRef<number | null>(null);

    const [showUpload, setShowUpload] = useState(false);
    const [uploadTitle, setUploadTitle] = useState('');
    const [uploadText, setUploadText] = useState('');
    const [uploadFile, setUploadFile] = useState<File | null>(null);
    const [uploadFileName, setUploadFileName] = useState('');
    const [uploading, setUploading] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
    const [editMode, setEditMode] = useState(false);
    const [selectedBooks, setSelectedBooks] = useState<Set<number>>(new Set());
    const batchFileRef = useRef<HTMLInputElement>(null);
    const [showBar, setShowBar] = useState(false);
    const [fabVisible, setFabVisible] = useState(false);
    const [humanName, setHumanName] = useState(() => localStorage.getItem('coread-human-name') || 'human');
    const [aiName, setAiName] = useState(() => COREAD_PERSONA_NAME || localStorage.getItem('coread-ai-name') || 'AI');
    const [showSettings, setShowSettings] = useState(false);
    const [showMenu, setShowMenu] = useState(false);
    const [menuPhase, setMenuPhase] = useState<'closed' | 'anim' | 'open' | 'closing'>('closed');
    const [menuDetail, setMenuDetail] = useState<'records' | 'manage' | 'backup' | null>(null);
    const [panelPhase, setPanelPhase] = useState<'closed' | 'anim' | 'open' | 'closing'>('closed');
    const [panelDetail, setPanelDetail] = useState<'file' | 'text' | null>(null);
    const [manageAction, setManageAction] = useState<'delete' | 'cache' | null>(null);
    const [confirmBatchDelete, setConfirmBatchDelete] = useState(false);
    const [backupBusy, setBackupBusy] = useState(false);
    const [backupFiles, setBackupFiles] = useState<{ file: string; bytes: number; mtime: string }[] | null>(null);
    const [restorePreview, setRestorePreview] = useState<{ token: string; counts: any; label: string } | null>(null);
    const [fileReading, setFileReading] = useState(false);
    const menuRef = useRef<HTMLDivElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const restoreFileRef = useRef<HTMLInputElement>(null);
    const [statsPhase, setStatsPhase] = useState<'closed' | 'open' | 'closing'>('closed');
    const [statsScope, setStatsScope] = useState<'global' | 'book'>('global');
    const [statsView, setStatsView] = useState<'shen' | 'mizunashi'>(() => (localStorage.getItem('coread-stats-view') as any) || 'shen');
    const [showJournal, setShowJournal] = useState(false);
    const [readingStats, setReadingStats] = useState<any>(null);
    const [readingStatsLoading, setReadingStatsLoading] = useState(false);
    const [statsOpenSeq, setStatsOpenSeq] = useState(0);
    const [doorLocked, setDoorLocked] = useState(false);
    const [lockPhase, setLockPhase] = useState<'closed' | 'open' | 'closing'>('closed');
    const [lockIntent, setLockIntent] = useState(true);
    const [statsCaption, setStatsCaption] = useState(false);
    const [statsReader, setStatsReader] = useState<'tongbao' | 'ai'>('tongbao');
    const [readerFontSize, setReaderFontSize] = useState(() => parseInt(localStorage.getItem('coread-font-size') || '14', 10));
    const [showFontPanel, setShowFontPanel] = useState(false);
    const [readerBrightness, setReaderBrightness] = useState(() => parseInt(localStorage.getItem('coread-brightness') || '100', 10));
    const [readerNightMode, setReaderNightMode] = useState(() => localStorage.getItem('coread-night-mode') === 'true');
    const [showSessionClock, setShowSessionClock] = useState(() => localStorage.getItem('coread-show-session-clock') === 'true');
    const [readerMenuPhase, setReaderMenuPhase] = useState<'closed' | 'anim' | 'open' | 'closing'>('closed');
    const [sheetPhase, setSheetPhase] = useState<'closed' | 'open' | 'closing'>('closed');
    const [allNotesPhase, setAllNotesPhase] = useState<'closed' | 'open' | 'closing'>('closed');
    const [pageTurnMode, setPageTurnMode] = useState<'swipe' | 'tap'>(() => (localStorage.getItem('coread-page-turn-mode') as any) || 'tap');
    // 版式：自动（横屏够宽摊两页）｜单页｜双页
    const [pageLayout, setPageLayout] = useState<PageLayout>(() => { const v = lsGet('coread-page-layout'); return v === 'single' || v === 'double' ? v : 'auto'; });
    const openReaderMenu = () => { setReaderMenuPhase('anim'); setTimeout(() => setReaderMenuPhase(p => p === 'anim' ? 'open' : p), 330); };
    const closeReaderMenu = () => { setReaderMenuPhase('closing'); setTimeout(() => setReaderMenuPhase(p => p === 'closing' ? 'closed' : p), 180); };
    const displayName = (from: string) => {
        const lower = from.toLowerCase();
        if (lower === 'human' || lower === humanName.toLowerCase()) return humanName;
        if (lower === 'ai' || lower === aiName.toLowerCase()) return aiName;
        return from;
    };
    const barTimer = useRef<any>(null);
    const touchStart = useRef<{ x: number; y: number; t: number } | null>(null);
    const suppressTapRef = useRef(false);
    const suppressTapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    const toggleBar = () => {
        if (activeComments.length > 0) setActiveComments([]);
        else setFabVisible(v => !v);
    };

    // 辅助阅读（阅读尺）：当前行黑字+淡框，其余行盖半透明底色变浅；点按/键盘逐行走
    const [lineFocus, setLineFocus] = useState(() => lsGet('coread-line-focus') === 'true');
    const [focusLines, setFocusLines] = useState<LineRect[]>([]);
    const [focusIdx, setFocusIdx] = useState(0);
    // 逐行 / 逐段：只改框住的范围和走一步的跨度，不影响排版，不进分页缓存键
    const [focusUnit, setFocusUnit] = useState<FocusUnit>(() => lsGet('coread-line-focus-unit') === 'para' ? 'para' : 'line');
    const focusLandRef = useRef<'first' | 'last'>('first');
    const focusPageKeyRef = useRef('');
    // 辅助阅读抽屉：切词/加粗/放大/间距；没有 Intl.Segmenter 的老浏览器前三项静默不生效
    const [assistPhase, setAssistPhase] = useState<'closed' | 'open' | 'closing'>('closed');
    const [assistSplit, setAssistSplit] = useState(() => lsGet('coread-assist-split') === 'true');
    const [assistBold, setAssistBold] = useState(() => lsGet('coread-assist-bold') === 'true');
    const [assistEnlarge, setAssistEnlarge] = useState(() => lsGet('coread-assist-enlarge') === 'true');
    const [assistSpacingLv, setAssistSpacingLv] = useState<SpacingLevel>(() => { const v = parseInt(lsGet('coread-assist-spacing') || '0', 10); return v === 1 || v === 2 ? v : 0; });
    const segmenter = useMemo(() => createSegmenter(), []);
    const assistOpts: AssistOpts = useMemo(() => segmenter
        ? { split: assistSplit, bold: assistBold, enlarge: assistEnlarge }
        : { split: false, bold: false, enlarge: false }, [segmenter, assistSplit, assistBold, assistEnlarge]);
    const assistOn = assistActive(assistOpts);
    const assistSig = `${assistOpts.split ? 's' : ''}${assistOpts.bold && !assistOpts.enlarge ? 'b' : ''}${assistOpts.enlarge ? 'e' : ''}${assistSpacingLv ? `g${assistSpacingLv}` : ''}`;
    const tokenCacheRef = useRef<Map<string, AssistToken[]>>(new Map());
    const tokensFor = (text: string): AssistToken[] => {
        const cache = tokenCacheRef.current;
        let t = cache.get(text);
        if (!t) {
            t = segmenter ? splitAssistTokens(text, segmenter) : [{ text, kind: 'content' }];
            if (cache.size > 4000) cache.clear();
            cache.set(text, t);
        }
        return t;
    };
    const assistFor = (fullText: string) => assistOn && !HAS_ENTITY.test(fullText);
    const closeAssistSheet = () => { setAssistPhase('closing'); setTimeout(() => setAssistPhase(p => p === 'closing' ? 'closed' : p), 240); };

    // ⋯ menu phase machine
    useEffect(() => {
        let t: ReturnType<typeof setTimeout> | null = null;
        if (showMenu) {
            if (menuPhase === 'closed' || menuPhase === 'closing') setMenuPhase('anim');
            else if (menuPhase === 'anim') t = setTimeout(() => setMenuPhase('open'), 330);
        } else {
            if (menuPhase === 'anim' || menuPhase === 'open') setMenuPhase('closing');
            else if (menuPhase === 'closing') t = setTimeout(() => { setMenuPhase('closed'); setMenuDetail(null); }, 180);
        }
        return () => { if (t) clearTimeout(t); };
    }, [showMenu, menuPhase]);
    // + upload panel phase machine
    useEffect(() => {
        let t: ReturnType<typeof setTimeout> | null = null;
        if (showUpload) {
            if (panelPhase === 'closed' || panelPhase === 'closing') setPanelPhase('anim');
            else if (panelPhase === 'anim') t = setTimeout(() => setPanelPhase('open'), 330);
        } else {
            if (panelPhase === 'anim' || panelPhase === 'open') setPanelPhase('closing');
            else if (panelPhase === 'closing') t = setTimeout(() => setPanelPhase('closed'), 180);
        }
        return () => { if (t) clearTimeout(t); };
    }, [showUpload, panelPhase]);

    const POP_EASE_INOUT = 'cubic-bezier(0.77, 0, 0.175, 1)';
    const POP_EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
    const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const viewsOf = (cEl: HTMLDivElement | null) => ({
        c: cEl,
        list: cEl?.querySelector('.cr-view-list') as HTMLElement | null,
        detail: cEl?.querySelector('.cr-view-detail') as HTMLElement | null,
    });
    const lockPopHeight = (cEl: HTMLDivElement | null) => {
        const { c: el, list } = viewsOf(cEl);
        if (el && list) el.style.height = `${list.offsetHeight + 10}px`;
    };
    const settlePop = (cEl: HTMLDivElement | null, expanded: boolean) => {
        const { c: el, list, detail } = viewsOf(cEl);
        if (!el || !list || !detail) return;
        el.classList.toggle('cr-show-detail', expanded);
        el.style.height = `${(expanded ? detail : list).offsetHeight + 10}px`;
        el.getAnimations({ subtree: true }).forEach(a => a.cancel());
    };
    const expandPop = (cEl: HTMLDivElement | null) => {
        const { c: el, list, detail } = viewsOf(cEl);
        if (!el || !list || !detail) return;
        if (reducedMotion()) { settlePop(cEl, true); return; }
        const h1 = el.offsetHeight, h2 = detail.offsetHeight + 10;
        el.animate([{ height: `${h1}px` }, { height: `${h2}px` }], { duration: 240, easing: POP_EASE_INOUT, fill: 'both' });
        list.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 100, easing: 'ease-out', fill: 'both' });
        detail.animate(
            [{ opacity: 0, transform: 'translateY(4px)', visibility: 'visible' },
             { opacity: 1, transform: 'translateY(0)', visibility: 'visible' }],
            { duration: 200, delay: 80, easing: POP_EASE_OUT, fill: 'both' });
        window.setTimeout(() => settlePop(cEl, true), 290);
    };
    const collapsePop = (cEl: HTMLDivElement | null) => {
        const { c: el, list, detail } = viewsOf(cEl);
        if (!el || !list || !detail) return;
        if (reducedMotion()) { settlePop(cEl, false); return; }
        const h1 = el.offsetHeight, h2 = list.offsetHeight + 10;
        el.animate([{ height: `${h1}px` }, { height: `${h2}px` }], { duration: 220, easing: POP_EASE_INOUT, fill: 'both' });
        detail.animate([{ opacity: 1, visibility: 'visible' }, { opacity: 0, visibility: 'visible' }], { duration: 180, easing: 'ease-out', fill: 'both' });
        list.animate(
            [{ opacity: 0, transform: 'translateY(4px)', visibility: 'visible' },
             { opacity: 1, transform: 'translateY(0)', visibility: 'visible' }],
            { duration: 180, delay: 100, easing: POP_EASE_OUT, fill: 'both' });
        window.setTimeout(() => settlePop(cEl, false), 280);
    };
    useLayoutEffect(() => { if (menuPhase === 'anim') lockPopHeight(menuRef.current); }, [menuPhase]);
    useLayoutEffect(() => { if (panelPhase === 'anim') lockPopHeight(panelRef.current); }, [panelPhase]);
    useLayoutEffect(() => { if (menuPhase === 'closing') settlePop(menuRef.current, false); }, [menuPhase]);
    useLayoutEffect(() => { if (panelPhase === 'closing') settlePop(panelRef.current, false); }, [panelPhase]);
    useLayoutEffect(() => { if (menuDetail) expandPop(menuRef.current); }, [menuDetail]);
    useLayoutEffect(() => {
        if (menuPhase !== 'open' || menuDetail !== 'backup') return;
        const { c: el, detail } = viewsOf(menuRef.current);
        if (!el || !detail) return;
        if (reducedMotion()) { settlePop(menuRef.current, true); return; }
        const h1 = el.offsetHeight, h2 = detail.offsetHeight + 10;
        if (h1 === h2) return;
        el.getAnimations().forEach(a => a.cancel());
        el.animate([{ height: `${h1}px` }, { height: `${h2}px` }], { duration: 220, easing: POP_EASE_INOUT, fill: 'both' });
        window.setTimeout(() => settlePop(menuRef.current, true), 230);
    }, [backupFiles, menuDetail, menuPhase]);
    useEffect(() => { if (menuPhase === 'closed') { setBackupFiles(null); setRestorePreview(null); } }, [menuPhase]);
    const openMenuDetail = (d: 'records' | 'manage' | 'backup') => { setBackupFiles(null); lockPopHeight(menuRef.current); setMenuDetail(d); };
    const openPanelDetail = (d: 'file' | 'text') => { setShowUpload(false); setPanelDetail(d); };
    const closeMenuDetail = () => { collapsePop(menuRef.current); window.setTimeout(() => setMenuDetail(null), 110); };
    const closePanelDetail = () => { if (!uploading) setPanelDetail(null); };

    useEffect(() => { loadBooks(); loadDoor(); }, []);

    useEffect(() => {
        const H = (window as any).Hyalite;
        if (!H || !H.supported()) return;
        const shelf = H.watch(document.body, '.hy-shelf', { bevel: 37, thickness: 59, slope: 2.7, shade: 0.46, rim: 1.76, edgeW: 8, sat: 0.86, edge: 0.32, blur: 1, dispersion: 1.6, light: -140, smooth: 1 });
        const reader = H.watch(document.body, '.hy-reader', { bevel: 73, thickness: 41, slope: 1.4, shade: 0.2, rim: 0.36, edgeW: 3, sat: 0.9, edge: 0.1, blur: 4, dispersion: 2.5, light: -150, smooth: 1.25 });
        return () => { shelf.stop(); reader.stop(); };
    }, []);

    // Android back button: push history state for each overlay, pop closes overlay
    useEffect(() => {
        const layers = [
            { open: confirmBatchDelete, close: () => setConfirmBatchDelete(false), tag: 'batchDel' },
            { open: restorePreview !== null, close: () => setRestorePreview(null), tag: 'restore' },
            { open: confirmDelete !== null, close: () => setConfirmDelete(null), tag: 'confirmDel' },
            { open: panelDetail !== null, close: closePanelDetail, tag: 'panelDetail' },
            { open: showSettings, close: () => setShowSettings(false), tag: 'settings' },
            { open: panelPhase !== 'closed', close: () => { if (!uploading) setShowUpload(false); }, tag: 'upload' },
            { open: menuPhase !== 'closed', close: () => { if (menuDetail) closeMenuDetail(); else setShowMenu(false); }, tag: 'dotMenu' },
            { open: lockPhase !== 'closed', close: closeLockDrawer, tag: 'lock' },
            { open: statsPhase !== 'closed', close: closeStatsDrawer, tag: 'stats' },
            { open: allNotesPhase !== 'closed', close: () => { setAllNotesPhase('closing'); setTimeout(() => setAllNotesPhase(p => p === 'closing' ? 'closed' : p), 240); }, tag: 'allNotes' },
            { open: assistPhase !== 'closed', close: () => { if (assistPhase !== 'closing') closeAssistSheet(); }, tag: 'assist' },
            { open: sheetPhase !== 'closed', close: () => { setSheetPhase('closing'); setTimeout(() => setSheetPhase(p => p === 'closing' ? 'closed' : p), 240); }, tag: 'sheet' },
            { open: readerMenuPhase !== 'closed', close: closeReaderMenu, tag: 'menu' },
            { open: showReplies, close: () => setShowReplies(false), tag: 'replies' },
        ];
        const topLayer = layers.find(l => l.open);
        if (!topLayer) return;
        const tag = topLayer.tag;
        if (!history.state?.coreadLayer) history.pushState({ coreadLayer: tag }, '');
        const onPop = (e: PopStateEvent) => { topLayer.close(); };
        window.addEventListener('popstate', onPop);
        return () => { window.removeEventListener('popstate', onPop); };
    }, [confirmBatchDelete, confirmDelete, lockPhase, menuDetail, menuPhase, panelDetail, panelPhase, restorePreview, showReplies, showSettings, statsPhase, allNotesPhase, assistPhase, sheetPhase, readerMenuPhase, uploading]);

    // Real-time comment sync — low-priority update, no flash
    useEffect(() => { commentsRef.current = comments; }, [comments]);
    useEffect(() => { allCommentsRef.current = allComments; }, [allComments]);

    const readingClock = useReadingClock(activeBook?.id || null,
        mode === 'reading' && !readingLoading && allParas.length > 0 && !showSettings && statsPhase === 'closed');


    const lastCommentIds = useRef('');
    useEffect(() => {
        if (mode !== 'reading' || !activeBook) return;
        const interval = setInterval(async () => {
            try {
                const d = await api.fetchBookDetail(activeBook.id, page);
                if (d.comments) {
                    const newIds = d.comments.map((c: any) => c.id).join(',');
                    if (newIds !== lastCommentIds.current) {
                        lastCommentIds.current = newIds;
                        startTransition(() => {
                            const mergeComments = (prev: Comment[]) => {
                                const merged = new Map(prev.map(c => [c.id, c]));
                                d.comments.forEach((cmt: Comment) => {
                                    const tempDup = Array.from(merged.values()).find(c => c.id !== cmt.id && c.content === cmt.content && c.from_who === cmt.from_who && c.paragraph_idx === cmt.paragraph_idx && c.reply_to === cmt.reply_to && Math.abs(new Date(c.created_at).getTime() - new Date(cmt.created_at).getTime()) < 10000);
                                    if (tempDup) merged.delete(tempDup.id);
                                    merged.set(cmt.id, cmt);
                                });
                                return Array.from(merged.values());
                            };
                            const bookId = activeBook.id;
                            setAllComments(prev => { const u = mergeComments(prev); cacheComments(bookId, u); return u; });
                            setComments(mergeComments);
                        });
                    }
                }
            } catch {}
        }, 3000);
        return () => clearInterval(interval);
    }, [mode, activeBook?.id, page]);

    // Poll for new replies from the AI
    useEffect(() => {
        if (mode !== 'reading' || !activeBook) return;
        const check = async () => {
            try {
                const lastSeen = parseInt(localStorage.getItem(`book-${activeBook.id}-last-seen`) || '0');
                const r = await fetch(`${BASE}/v1/books/${activeBook.id}/new-replies?since=${lastSeen}`, { headers: { 'x-coread-key': COREAD_ACCESS_KEY } });
                if (r.ok) {
                    const d = await r.json();
                    const aiOnly = (d.replies || []).filter((r: any) => r.from_who.toLowerCase() !== humanName.toLowerCase());
                    setNewReplies(aiOnly.length ? aiOnly : []);
                }
            } catch {}
        };
        check();
        const interval = setInterval(check, 5000);
        return () => clearInterval(interval);
    }, [mode, activeBook?.id]);

    const dismissReplies = () => {
        if (activeBook && newReplies.length) {
            const maxId = Math.max(...newReplies.map(r => r.id));
            localStorage.setItem(`book-${activeBook.id}-last-seen`, String(maxId));
        }
        setNewReplies([]);
        setShowReplies(false);
    };

    const findPageForParaIdx = (paraIdx: number, maxPages = totalPages, charOffset = 0) => {
        const targetParaIdx = Number(paraIdx);
        const targetOffset = Number(charOffset) || 0;
        let paraIndex = allParas.findIndex(p => Number(p.idx) === targetParaIdx);
        if (paraIndex < 0) paraIndex = allParas.findIndex(p => Number(p.idx) >= targetParaIdx);
        if (paraIndex < 0) return -1;

        const lastPage = Math.min(maxPages, pageBreaks.length) - 1;
        // 二分：breaks 按 (paraIndex, offset) 单调递增；大书目录几千章逐个换算页码，线性扫会卡
        let lo = 0, hi = lastPage, ans = 0;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const br = pageBreaks[mid];
            if (br.paraIndex < paraIndex || (br.paraIndex === paraIndex && br.offset <= targetOffset)) { ans = mid; lo = mid + 1; }
            else hi = mid - 1;
        }
        return ans;
    };

    // 后台补全分页中：临时页表只覆盖当前位置附近，窗外目标等全书分页完成再跳
    const canJumpToPara = (idx: number) => {
        const pr = provisionalRangeRef.current;
        if (!pr) return true;
        const tpi = allParas.findIndex(p => Number(p.idx) >= Number(idx));
        if (tpi >= pr.from && tpi < pr.to) return true;
        toast('全书分页后台补全中，完成后再跳');
        return false;
    };

    const resolveNoticeTarget = (notice: ReplyNotice, pool: Comment[]) => {
        const existing = pool.find(c => c.id === notice.id);
        const replyTo = notice.reply_to ?? notice.parent_id ?? existing?.reply_to ?? null;
        const parent = replyTo ? pool.find(c => c.id === replyTo) : null;
        const target = parent || existing;
        const fallbackPara = Number(notice.parent_paragraph_idx ?? notice.paragraph_idx);
        const fallbackOffset = Number(notice.parent_sel_start_idx ?? notice.sel_start_idx);
        return {
            existing,
            replyTo,
            parent,
            paraIdx: Number(target?.paragraph_idx ?? fallbackPara),
            offset: Number.isFinite(Number(target?.sel_start_idx)) ? Number(target?.sel_start_idx) : (Number.isFinite(fallbackOffset) ? fallbackOffset : 0),
        };
    };

    const rememberReturnPoint = () => {
        setReturnPoint(prev => prev || { page, paraIdx: currentParaIdxRef.current ?? paragraphs[0]?.idx ?? null });
    };

    const returnToReadingPosition = () => {
        if (!returnPoint) return;
        setActiveComments([]);
        setShowReplies(false);
        setShowBar(false);
        const targetPage = returnPoint.paraIdx != null ? findPageForParaIdx(returnPoint.paraIdx) : -1;
        setPage(targetPage >= 0 ? targetPage + 1 : Math.max(1, Math.min(totalPages, returnPoint.page)));
        setReturnPoint(null);
    };

    const openReplyNotice = (notice: ReplyNotice) => {
        rememberReturnPoint();
        setShowReplies(false);
        setShowBar(false);
        const pool = Array.from(new Map([...allCommentsRef.current, ...commentsRef.current].map(c => [c.id, c])).values());
        const { existing, replyTo, parent, paraIdx: targetParaIdx, offset: targetOffset } = resolveNoticeTarget(notice, pool);
        const targetPage = findPageForParaIdx(targetParaIdx, totalPages, targetOffset);
        if (targetPage >= 0) setPage(targetPage + 1);
        const noticeComment: Comment = existing || {
            id: notice.id,
            book_id: activeBook?.id ?? 0,
            paragraph_idx: targetParaIdx,
            sel_end_para_idx: null,
            sel_start_idx: targetOffset,
            sel_end_idx: notice.parent_sel_end_idx ?? notice.sel_end_idx ?? null,
            selected_text: notice.parent_selected_text ?? notice.selected_text ?? null,
            from_who: notice.from_who || 'ai',
            content: notice.content,
            created_at: notice.created_at || new Date().toISOString(),
            reply_to: replyTo,
        };
        const thread = parent
            ? [parent, ...pool.filter(c => c.reply_to === parent.id || c.id === noticeComment.id)]
            : [noticeComment];
        setActiveComments(thread.some(c => c.id === noticeComment.id) ? thread : [...thread, noticeComment]);
        if (!existing) {
            setComments(prev => prev.some(c => c.id === noticeComment.id) ? prev : [...prev, noticeComment]);
            setAllComments(prev => prev.some(c => c.id === noticeComment.id) ? prev : [...prev, noticeComment]);
        }
    };

    // Selection change listener for floating annotation bar
    useEffect(() => {
        if (mode !== 'reading') return;
        const findPara = (n: Node): HTMLElement | null => {
            let el: HTMLElement | null = (n.nodeType === Node.TEXT_NODE ? n.parentElement : n) as HTMLElement;
            while (el && !(el as any).dataset?.paraIdx) el = el.parentElement;
            return el;
        };
        const handler = () => {
            const sel = window.getSelection();
            if (!sel || !sel.toString().trim() || sel.rangeCount === 0) { setFloatingBar(null); return; }
            const range = sel.getRangeAt(0);
            const startEl = findPara(range.startContainer);
            const endEl = findPara(range.endContainer);
            if (!startEl || !endEl) { setFloatingBar(null); return; }

            const startPara = parseInt((startEl as any).dataset.paraIdx);
            const endPara = parseInt((endEl as any).dataset.paraIdx);
            const text = sel.toString().trim();
            try {
                const pre1 = document.createRange();
                pre1.selectNodeContents(startEl);
                pre1.setEnd(range.startContainer, range.startOffset);
                const startBase = parseInt((startEl as any).dataset.fragStart || '0');
                const endBase = parseInt((endEl as any).dataset.fragStart || '0');
                const startOff = startBase + pre1.toString().length;

                let endOff: number;
                if (startPara === endPara) {
                    endOff = startOff + text.length;
                } else {
                    const pre2 = document.createRange();
                    pre2.selectNodeContents(endEl);
                    pre2.setEnd(range.endContainer, range.endOffset);
                    endOff = endBase + pre2.toString().length;
                }
                setFloatingBar({ startPara, endPara, text, start: startOff, end: endOff });
            } catch { setFloatingBar(null); }
        };
        document.addEventListener('selectionchange', handler);
        return () => document.removeEventListener('selectionchange', handler);
    }, [mode]);

    const loadBooks = async (silent = false) => {
        if (!silent) setLoading(true);
        setError('');
        try { const d = await api.fetchBooks(); setBooks(d.books || []); }
        catch (e: any) { setError(e.message); }
        setLoading(false);
    };

    useEffect(() => {
        if (!READ_TOGETHER || (mode !== 'shelf' && mode !== 'reading')) return;
        let alive = true;
        const start = async () => {
            if (oitChatSessionRef.current) return;
            try {
                const personaPid = new URL(window.location.href).searchParams.get('persona_pid') || '';
                const session: any = await api.oitChat('start', { persona_pid: personaPid });
                if (alive) oitChatSessionRef.current = session.session_id || '';
            } catch (e: any) { if (alive) setOitChatStatus(`OIT 沟通连接失败: ${e.message}`); }
        };
        start();
        return () => { alive = false; };
    }, [mode]);

    useEffect(() => {
        if (mode !== 'shelf' || !READ_TOGETHER) return;
        const timer = window.setInterval(() => loadBooks(true), 5000);
        return () => window.clearInterval(timer);
    }, [mode]);

    const loadDoor = async () => {
        try { const d = await api.fetchRoomDoor(); setDoorLocked(!!d.locked); } catch {}
    };
    const toggleDoor = () => {
        setLockIntent(!doorLocked);
        setLockPhase('open');
        setShowMenu(false);
    };
    const closeLockDrawer = () => { setLockPhase('closing'); setTimeout(() => setLockPhase(p => p === 'closing' ? 'closed' : p), 240); };
    const confirmDoor = async () => {
        try {
            const d = await api.setRoomDoor(lockIntent);
            setDoorLocked(!!d.locked);
            toast(d.locked ? '已关门上锁 🔒' : '已开门 🔓');
        } catch (e: any) { toast(`操作失败: ${e.message}`); }
        closeLockDrawer();
    };

    const fmtBackupTime = (iso: string) => { const d = new Date(iso); if (isNaN(d.getTime())) return iso; const p = (n: number) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; };
    const doBackupExport = async () => {
        if (backupBusy) return;
        setBackupBusy(true);
        try {
            const settings = {
                'coread-font-size': localStorage.getItem('coread-font-size') || '',
                'coread-brightness': localStorage.getItem('coread-brightness') || '',
                'coread-night-mode': localStorage.getItem('coread-night-mode') || '',
                'coread-reading-clock': localStorage.getItem('coread-reading-clock') || '',
                'coread-tap-turn': localStorage.getItem('coread-tap-turn') || '',
            };
            const d = await api.backupExport(settings);
            toast(`已存到服务器（${(d.bytes / 1048576).toFixed(0)}MB），在「恢复备份」里能看到`);
        } catch (e: any) { toast(e.message || '导出失败'); }
        setBackupBusy(false);
    };
    const doBackupList = async () => {
        if (backupFiles !== null) { setBackupFiles(null); return; }
        try {
            const d = await api.backupList();
            setBackupFiles(d.files || []);
        } catch { toast('读备份列表失败'); }
    };
    const doBackupPreview = async (payload: any, label: string) => {
        if (backupBusy) return;
        setBackupBusy(true);
        try {
            const d = await api.backupPreview(payload);
            setRestorePreview({ token: d.token, counts: d, label });
        } catch (e: any) { toast(e.message || '预检失败'); }
        setBackupBusy(false);
    };
    const doBackupRestore = async () => {
        if (!restorePreview) return;
        const token = restorePreview.token;
        setRestorePreview(null);
        setBackupBusy(true);
        try {
            const d = await api.backupRestore(token);
            try { for (let i = localStorage.length - 1; i >= 0; i--) { const k = localStorage.key(i); if (k && (k.startsWith('pagebreaks-') || k.startsWith('coread-front-progress-'))) localStorage.removeItem(k); } } catch {}
            try { indexedDB.deleteDatabase('study-reader-cache'); } catch {}
            toast(`恢复完成：${d.counts.books} 本书回来了`);
            loadBooks();
        } catch (e: any) { toast(e.message || '恢复失败'); }
        setBackupBusy(false);
    };
    const onRestoreFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const f = e.target.files?.[0];
        e.target.value = '';
        if (!f) return;
        if (f.size > 60 * 1024 * 1024) { toast('备份文件太大，改用服务器上已有的备份恢复'); return; }
        try { await doBackupPreview({ backup: JSON.parse(await f.text()) }, f.name); }
        catch { toast('备份文件读不出来，是有效的备份 JSON 吗？'); }
    };

    const loadReadingStats = (reader: 'tongbao' | 'ai') => {
        setStatsReader(reader);
        setReadingStatsLoading(true);
        fetch(`${BASE}/v1/reading-stats?today=${localDay(Date.now())}&reader=${reader}`, { headers: { 'x-owner-key': ROOM_OWNER_KEY, 'x-coread-key': COREAD_ACCESS_KEY } })
            .then(r => r.json()).then(d => { setReadingStats(d); setReadingStatsLoading(false); })
            .catch(() => setReadingStatsLoading(false));
    };

    const openStatsDrawer = async (scope: 'global' | 'book') => {
        setReadingStatsLoading(true);
        try { await flushReading(); setReadingStats(await api.fetchReadingStats(localDateString())); }
        catch (e: any) { toast(`阅读记录加载失败: ${e.message}`); }
        setReadingStatsLoading(false);
        if (statsView === 'mizunashi') { setShowJournal(true); return; }
        setStatsScope(scope);
        setStatsPhase('open');
        setStatsOpenSeq(s => s + 1);
    };
    const closeStatsDrawer = () => { setStatsPhase('closing'); setTimeout(() => setStatsPhase(p => p === 'closing' ? 'closed' : p), 240); };

    const openRequest = useRef(0);
    const openBook = async (book: Book) => {
        const requestId = ++openRequest.current;
        window.dispatchEvent(new Event('coread-pause'));
        if (READ_TOGETHER && !oitChatSessionRef.current) { toast('请等待 OIT 沟通连接就绪后重试'); return; }
        setOitChatStatus('');
        if (requestId !== openRequest.current) return;
        api.touchBookOpen(book.id).catch(() => {});
        setActiveBook(book); setMode('reading');
        setReadingLoading(true);
        setPage(1); setTotalPages(1); setPageBreaks([{ paraIndex: 0, offset: 0 }]); setPageFragments([]); setPaginateProgress(null);
        setParagraphs([]); setComments([]); setAllParas([]); setAllComments([]);
        currentParaIdxRef.current = null;
        anchorPosRef.current = null;
        savedParaOffsetRef.current = 0;
        provisionalRangeRef.current = null;
        try {
            const totalParas = Math.max(1, book.total_paragraphs || 0);
            const paraCacheKey = `paras-v1-${book.id}`;
            const commentCacheKey = `comments-v1-${book.id}`;
            let cacheHit = false;
            // 段落内容也进 IndexedDB：二次打开跳过网络拉取（大书13万段逐块拉要约1-2分钟）
            try {
                const cached = await idbGetParas(paraCacheKey);
                if (requestId !== openRequest.current) return;
                if (cached) {
                    const parsed = JSON.parse(cached);
                    if (parsed.totalParas === totalParas && Array.isArray(parsed.paragraphs) && parsed.paragraphs.length > 0) {
                        setAllParas(parsed.paragraphs);
                        const cachedComments = await idbGetParas(commentCacheKey);
                        if (requestId !== openRequest.current) return;
                        const comments = cachedComments ? JSON.parse(cachedComments) : [];
                        setAllComments(comments);
                        setComments(comments);
                        savedParaIdxRef.current = book.current_paragraph_idx ?? 0;
                        savedParaOffsetRef.current = book.current_paragraph_offset ?? 0;
                        cacheHit = true;
                    }
                }
            } catch {}
            if (!cacheHit) {
                // 全书分块拉取：小书1块、大书多块（避免单次大响应内存峰值，也修掉9999段截断）；批注随块增量合并
                const rawParas: Paragraph[] = [];
                const fetchedComments: Comment[] = [];
                const seenCommentIds = new Set<number>();
                for (let start = 0; start < totalParas; start += PARA_FETCH_CHUNK) {
                    const d = await api.fetchBookSlice(book.id, start, PARA_FETCH_CHUNK);
                    if (requestId !== openRequest.current) return;
                    rawParas.push(...(d.paragraphs || []));
                    for (const cmt of (d.comments || []) as Comment[]) {
                        if (!seenCommentIds.has(cmt.id)) { seenCommentIds.add(cmt.id); fetchedComments.push(cmt); }
                    }
                    if ((d.paragraphs || []).length < PARA_FETCH_CHUNK) break;
                }
                const isEpubJunk = (s: string) => /^(1UR057|Cover|封面|插图|导航|书名页|制作信息|Contents|[A-Z0-9]{3,10}(-\d+)?)$/.test(s.trim());
                const allP: Paragraph[] = rawParas.filter((p: Paragraph) => !isEpubJunk(p.content));
                // Hide TOC sections (目录 heading + consecutive chapter titles)
                const tocRe = /^(#\s*)?目录$/;
                const chRe = /^(第[\d一二三四五六七八九十百千万]+[章节回部篇]|序章|序$|终章|后记|尾声|附录|解说)/;
                let tocZone = false;
                const filtered = allP.filter(p => {
                    const t = p.content.trim();
                    if (tocRe.test(t)) { tocZone = true; return false; }
                    if (tocZone) { if (chRe.test(t) || t === '') return false; tocZone = false; }
                    return true;
                });
                setAllParas(filtered);
                setAllComments(fetchedComments);
                setComments(fetchedComments);
                savedParaIdxRef.current = book.current_paragraph_idx ?? 0;
                savedParaOffsetRef.current = book.current_paragraph_offset ?? 0;
                // 有内容时loading由分页effect跳页完成后关闭——这里提前关会先露出第1页再跳（闪烁）
                if (filtered.length === 0) setReadingLoading(false);
                idbSetParas(paraCacheKey, JSON.stringify({ paragraphs: filtered, totalParas })).catch(() => {});
                idbSetParas(commentCacheKey, JSON.stringify(fetchedComments)).catch(() => {});
            }
        } catch (e: any) { if (requestId === openRequest.current) { toast(`加载失败: ${e.message}`); setReadingLoading(false); } }
        api.fetchBookToc(book.id).then(d => { if (requestId === openRequest.current) setTocChapters(d.chapters || []); }).catch(() => {});
    };

    useLayoutEffect(() => {
        if (mode !== 'reading' || !contentRef.current) return;
        const el = contentRef.current;
        let frame = 0;
        const update = () => {
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                const width = Math.round(el.clientWidth);
                const height = Math.max(0, Math.round(el.clientHeight - READER_VERTICAL_PADDING_STATIC - getSafeAreaBottom()));
                setReaderSize(prev => {
                    if (prev.width === width && prev.height === height) return prev;
                    if (prev.height > 0 && height < prev.height * 0.8 && width === prev.width) {
                        const ae = document.activeElement as HTMLElement | null;
                        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable)) return prev;
                    }
                    return { width, height };
                });
            });
        };
        update();
        const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
        ro?.observe(el);
        window.addEventListener('resize', update);
        return () => {
            cancelAnimationFrame(frame);
            ro?.disconnect();
            window.removeEventListener('resize', update);
        };
    }, [mode]);

    // 自动：横屏且够宽（平板横拿、电脑）才摊两页，手机竖拿永远单页
    const { spread, contentWidth: readerContentWidth } = readerColumns(pageLayout, readerSize.width, readerSize.height, readerFontSize, READER_HORIZONTAL_PADDING, SPREAD_GUTTER, READER_MAX_LINE_CHARS);

    // 同书只存一份分页缓存，value里带测量时的尺寸，读取时容差校验。
    // 不能把精确像素拼进key：手机WebView每次打开视口差±几px，key永远miss，导致每次进书全书重新measure。
    const prevAssistSigRef = useRef<string | null>(null);
    const paginationCacheKey = activeBook ? `pagebreaks-v3-${activeBook.id}-fs${readerFontSize}${assistSig ? `-a${assistSig}` : ''}` : '';
    const imgHeightCache = useRef<Map<string, number>>(new Map());

    // 与 renderAssistText 同结构（词 span + 裸文本标点），分页量出来的才和显示一致
    const appendAssistNodes = (el: HTMLElement, fullText: string, start: number, end: number, fs: number) => {
        for (const t of placeTokens(tokensFor(fullText), start, end)) {
            const st = tokenStyle(t.kind, t.text, assistOpts, fs * 0.3, t.gapAfter, readerNightMode);
            if (!st) { el.appendChild(document.createTextNode(t.text)); continue; }
            const span = document.createElement('span');
            Object.assign(span.style, st);
            span.textContent = t.text;
            el.appendChild(span);
        }
    };

    const buildMeasureBlock = (para: Paragraph, sourceIdx: number, start: number, end: number) => {
        const heading = isHeading(para.content);
        const chapterTitle = isChapterStart(para.content);
        const outer = document.createElement('div');
        outer.style.marginTop = `${chapterTitle && start === 0 && sourceIdx > 0 ? CHAPTER_GAP_TOP : 0}px`;
        outer.style.marginBottom = `${chapterTitle ? CHAPTER_GAP_BOTTOM : PARA_GAP}px`;

        const imgMatch = para.content.match(/^\[IMG:([^\]]+)\]$/);
        if (imgMatch && start === 0) {
            const imgMaxH = Math.floor(readerSize.height * 0.6);
            const cachedH = imgHeightCache.current.get(imgMatch[1]);
            const h = cachedH ? Math.min(cachedH, imgMaxH) : imgMaxH;
            const imgEl = document.createElement('div');
            imgEl.style.height = `${h}px`;
            imgEl.style.width = '100%';
            outer.appendChild(imgEl);
        } else {
            const fullText = stripHeading(para.content);
            const displayText = fullText.slice(start, end);
            const inner = document.createElement('div');
            const fs = chapterTitle ? readerFontSize + 4 : para.content.trim().startsWith('# ') ? readerFontSize + 3 : para.content.trim().startsWith('## ') ? readerFontSize + 2 : readerFontSize;
            if (displayText && assistFor(fullText)) appendAssistNodes(inner, fullText, start, end, fs);
            else inner.textContent = displayText || ' ';
            const sp = assistSpacing(chapterTitle ? 2.2 : 1.85, chapterTitle ? 1 : 0.3, fs, assistSpacingLv);
            inner.style.fontSize = `${fs}px`;
            inner.style.lineHeight = String(sp.lineHeight);
            inner.style.letterSpacing = `${sp.letterSpacing}px`;
            inner.style.textIndent = heading || chapterTitle || start > 0 ? '0' : '1.5em';
            inner.style.fontWeight = String(chapterTitle ? 800 : heading ? 700 : 400);
            inner.style.textAlign = chapterTitle ? 'center' : '';
            inner.style.whiteSpace = 'pre-wrap';
            inner.style.overflowWrap = 'anywhere';
            outer.appendChild(inner);
        }


        return outer;
    };

    useEffect(() => {
        if (mode !== 'reading' || !measureRef.current || allParas.length === 0 || readerContentWidth <= 1 || readerSize.height <= 0) return;
        let cancelled = false;
        repaginatingRef.current = true;
        const run = async () => {
            await (document.fonts as any)?.ready?.catch(() => {});
            await new Promise<void>(r => requestAnimationFrame(() => r()));
            if (cancelled || !measureRef.current) return;

            if (activeBook && allParas.length <= PROGRESSIVE_MEASURE_THRESHOLD) {
                const imgParas = allParas.filter(p => /^\[IMG:([^\]]+)\]$/.test(p.content));
                const loadPromises = imgParas.map(p => {
                    const m = p.content.match(/^\[IMG:([^\]]+)\]$/);
                    if (!m || imgHeightCache.current.has(m[1])) return Promise.resolve();
                    return new Promise<void>(resolve => {
                        const img = new Image();
                        img.onload = () => {
                            const maxW = readerContentWidth;
                            const maxH = Math.floor(readerSize.height * 0.6);
                            const scale = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);
                            imgHeightCache.current.set(m![1], img.naturalHeight * scale);
                            resolve();
                        };
                        img.onerror = () => resolve();
                        img.src = api.imageUrl(activeBook!.id, m[1]);
                    });
                });
                await Promise.all(loadPromises);
            }
            if (cancelled || !measureRef.current) return;

            const measurer = measureRef.current;
            measurer.innerHTML = '';
            measurer.style.width = `${readerContentWidth}px`;
            const maxHeight = Math.max(100, readerSize.height - 8);
            setPageHeight(readerSize.height);
            const progressive = allParas.length > PROGRESSIVE_MEASURE_THRESHOLD;
            provisionalRangeRef.current = null;

            // 浏览器是唯一知道实际字号、宽高和断行结果的一端。把它测出的页首坐标
            // 同步给服务端，MCP 之后按同一张页表读书，而不是再估算一遍。
            const syncPagination = (resolvedBreaks: PageBreak[]) => {
                if (!activeBook || resolvedBreaks.length === 0) return;
                const serverBreaks = resolvedBreaks.map(b => {
                    const para = allParas[b.paraIndex];
                    return para ? { paragraph_idx: para.idx, offset: b.offset } : null;
                }).filter(Boolean);
                if (serverBreaks.length === 0) return;
                api.syncBookPagination(activeBook.id, {
                    version: 1,
                    source_paragraph_count: activeBook.total_paragraphs,
                    paragraph_ids: allParas.map(p => p.idx),
                    breaks: serverBreaks,
                    viewport: {
                        width: readerContentWidth,
                        height: readerSize.height,
                        font_size: readerFontSize,
                    },
                }).catch(() => {});
            };

            // 锚点（段号+段内字数）→ allParas 下标；段号不在了就落到它后面第一段的段首
            const resolveAnchor = (a: { idx: number; off: number }) => {
                const pi = allParas.findIndex(p => p.idx >= a.idx);
                return { pi, off: pi >= 0 && allParas[pi].idx === a.idx ? a.off : 0 };
            };
            // 锚点落在新页表的哪一页（0 起）
            const pageOf = (bks: PageBreak[], a: { idx: number; off: number }) => {
                const { pi, off } = resolveAnchor(a);
                if (pi < 0) return 0;
                for (let i = bks.length - 1; i >= 0; i--) {
                    if (bks[i].paraIndex < pi || (bks[i].paraIndex === pi && bks[i].offset <= off)) return i;
                }
                return 0;
            };

            // 后手优化判定——缓存miss且锚点够靠后时，先快速分当前位置±PROVISIONAL_WIN段的临时页
            // 立即可读，全书分页随后照常从0跑完后替换
            const startLiveIdx = currentParaIdxRef.current;
            let provisionalLandIdx: number | null = null;
            const anchorPos0 = savedParaIdxRef.current != null
                ? { idx: savedParaIdxRef.current, off: savedParaOffsetRef.current }
                : anchorPosRef.current ?? { idx: currentParaIdxRef.current ?? allParas[0]?.idx ?? 0, off: 0 };
            const anchorPi0 = resolveAnchor(anchorPos0).pi;
            const useProvisional = progressive && !suppressPageJumpRef.current && anchorPi0 > PROVISIONAL_WIN;

            // Try cache first（IndexedDB 主存；localStorage 里的旧缓存读出后迁移进IDB）
            if (paginationCacheKey) {
                try {
                    let cached = await idbGet(paginationCacheKey);
                    if (cancelled) return;
                    if (!cached) {
                        cached = localStorage.getItem(paginationCacheKey);
                        if (cached) idbSet(paginationCacheKey, cached);
                    }
                    if (cached) {
                        const { breaks: cachedBreaks, paraCount, width: cw, height: ch } = JSON.parse(cached);
                        // 尺寸容差：宽±2px严格（影响断行）；高±80px宽松——高度只影响每页容量/页尾留白，
                        // 大书重测要约1分钟，手机WebView每次打开视口抖几十px不该触发重分页
                        const sizeOk = typeof cw === 'number' && typeof ch === 'number'
                            && Math.abs(cw - readerContentWidth) <= 2 && Math.abs(ch - readerSize.height) <= 80;
                        if (sizeOk && paraCount === allParas.length && Array.isArray(cachedBreaks) && cachedBreaks.length > 0) {
                            setPageBreaks(cachedBreaks);
                            setTotalPages(Math.max(1, cachedBreaks.length));
                            syncPagination(cachedBreaks);
                            if (!suppressPageJumpRef.current) {
                                const targetPage = pageOf(cachedBreaks, anchorPos0);
                                setPage(Math.max(1, Math.min(cachedBreaks.length, targetPage + 1)));
                            }
                            repaginatingRef.current = false;
                            savedParaIdxRef.current = null;
                            savedParaOffsetRef.current = 0;
                            setReadingLoading(false);
                            return;
                        }
                    } else if (progressive) {
                        toast(useProvisional ? '无分页缓存，先打开当前位置，后台补全全书' : '无分页缓存，将完整分页一次');
                    }
                } catch {}
            }

            const breaks: PageBreak[] = [{ paraIndex: 0, offset: 0 }];

            // 连续排版 → 每块一次 layout，之后几何读取走缓存（layout 干净时读 rect 零 reflow）。
            // 旧算法逐段试塞，每段一次同步reflow，几千段=首开十几秒。
            // 切点全部落在行边界，所以续段在下一页重排时断行不变，几何坐标保持连续。
            // 大书（>PROGRESSIVE_MEASURE_THRESHOLD 段）分块挂载测量：块间让出主线程并更新进度，
            // 避免十几万段一次性 append 的内存峰值与长时间卡死；跨块用锚点段对齐逻辑纵坐标。
            const chapterTopGap = (i: number) =>
                isChapterStart(allParas[i].content) && i > 0 ? CHAPTER_GAP_TOP : 0;

            const measureRange = document.createRange();

            let blocks: HTMLElement[] = [];
            let blockRects: DOMRect[] = [];
            let chunkBase = 0;    // 当前块首段的数组下标
            let blockOffset = 0;  // 锚点段占位（1）或无（0）
            let rectShift = 0;    // 逻辑纵坐标 = DOM rect 读数 + rectShift
            let chunkEnd = 0;     // 当前块末段下标（不含）

            const blockIdx = (i: number) => i - chunkBase + blockOffset;
            const rectBottom = (i: number) => blockRects[blockIdx(i)].bottom + rectShift;
            const rectTop = (i: number) => blockRects[blockIdx(i)].top + rectShift;

            // 挂载 [from, from+MEASURE_CHUNK) 的测量块；anchorIdx=上一块末段（保留在容器顶做新旧坐标系对齐）
            const mountChunk = (from: number, anchorIdx: number | null, anchorLogicalBottom: number | null) => {
                measurer.innerHTML = '';
                blocks = [];
                if (anchorIdx != null && anchorIdx >= 0 && anchorIdx < from) {
                    const t0 = stripHeading(allParas[anchorIdx].content);
                    blocks.push(buildMeasureBlock(allParas[anchorIdx], anchorIdx, 0, t0.length));
                }
                blockOffset = blocks.length;
                const to = Math.min(allParas.length, from + MEASURE_CHUNK);
                for (let i = from; i < to; i++) {
                    const t = stripHeading(allParas[i].content);
                    blocks.push(buildMeasureBlock(allParas[i], i, 0, t.length));
                }
                for (const b of blocks) measurer.appendChild(b);
                blockRects = blocks.map(b => b.getBoundingClientRect());
                chunkBase = from;
                if (anchorIdx != null && anchorLogicalBottom != null && blockRects.length > 0) {
                    rectShift = anchorLogicalBottom - blockRects[0].bottom;
                }
            };

            // 段内文字可能拆在多个文本节点里（辅助阅读的词 span），按字符偏移映射回 (节点, 节点内偏移)
            interface TextIndex { nodes: Text[]; starts: number[]; length: number }
            const textIndexCache = new WeakMap<Element, TextIndex>();
            const innerText = (i: number): TextIndex | null => {
                const inner = blocks[blockIdx(i)]?.firstElementChild;
                if (!inner) return null;
                let ti = textIndexCache.get(inner);
                if (!ti) {
                    const nodes: Text[] = [], starts: number[] = [];
                    let len = 0;
                    const walker = document.createTreeWalker(inner, NodeFilter.SHOW_TEXT);
                    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
                        const t = n as Text;
                        if (!t.length) continue;
                        nodes.push(t); starts.push(len); len += t.length;
                    }
                    ti = { nodes, starts, length: len };
                    textIndexCache.set(inner, ti);
                }
                return ti.nodes.length ? ti : null;
            };
            // o 落在两节点交界时取前一节点的末尾，等价于单节点时 setEnd(node, o)
            const locate = (ti: TextIndex, o: number): [Text, number] => {
                let lo = 0, hi = ti.nodes.length - 1;
                while (lo < hi) {
                    const mid = (lo + hi + 1) >> 1;
                    if (ti.starts[mid] < o) lo = mid; else hi = mid - 1;
                }
                return [ti.nodes[lo], Math.min(o - ti.starts[lo], ti.nodes[lo].length)];
            };

            // 前o个字符的包络底（逻辑纵坐标，单调递增），layout干净时读rect零reflow
            const bottomAt = (ti: TextIndex, o: number) => {
                measureRange.setStart(ti.nodes[0], 0);
                const [n, k] = locate(ti, Math.min(o, ti.length));
                measureRange.setEnd(n, k);
                return measureRange.getBoundingClientRect().bottom + rectShift;
            };
            // 排满到limitY的最大行尾offset；一行都放不下返回0
            const lineCut = (i: number, limitY: number): number => {
                const ti = innerText(i);
                if (!ti || ti.length === 0) return 0;
                const len = ti.length;
                if (bottomAt(ti, len) <= limitY) return len;
                let lo = 1, hi = len, best = 0;
                while (lo <= hi) {
                    const mid = (lo + hi) >> 1;
                    if (bottomAt(ti, mid) <= limitY) { best = mid; lo = mid + 1; }
                    else hi = mid - 1;
                }
                return best;
            };

            // 段 i 不在已挂载块内时换块：保留 i-1 段做锚点，逻辑纵坐标无缝接续；大书块间让出主线程
            const ensureChunk = async (i: number): Promise<boolean> => {
                if (i >= allParas.length || i < chunkEnd) return true;
                const anchorLogicalBottom = i > 0 ? rectBottom(i - 1) : 0;
                // 小书也要块间让出主线程，不然切辅助阅读设置时整本书同步重排，抽屉动画卡住
                if (progressive) setPaginateProgress(Math.min(0.99, i / allParas.length));
                await new Promise<void>(r => setTimeout(r, 0));
                if (cancelled || !measureRef.current) return false;
                mountChunk(i, i > 0 ? i - 1 : null, anchorLogicalBottom);
                chunkEnd = i + blocks.length - blockOffset;
                return true;
            };

            // 后手优化：临时页只按段界切（不做段中切分），与最终页表允许有出入，很快会被全书分页替换
            let provisionalShown = false;
            if (useProvisional) {
                const wFrom = anchorPi0 - PROVISIONAL_WIN;
                const wTo = Math.min(allParas.length, anchorPi0 + PROVISIONAL_WIN);
                const wb: PageBreak[] = [{ paraIndex: wFrom, offset: 0 }];
                let i = wFrom;
                let wCursor = 0;
                let wGuard = 0;
                while (i < wTo && wGuard++ < 10000) {
                    if (i >= chunkEnd) {
                        mountChunk(i, i > wFrom ? i - 1 : null, i > wFrom ? rectBottom(i - 1) : 0);
                        chunkEnd = i + blocks.length - blockOffset;
                        if (i === wFrom) wCursor = rectTop(wFrom) - chapterTopGap(wFrom);
                        await new Promise<void>(r => setTimeout(r, 0)); // 块间让出主线程
                        if (cancelled || !measureRef.current) return;
                        continue;
                    }
                    const limitY = wCursor + maxHeight + 1;
                    let pageHasContent = false;
                    while (i < chunkEnd && i < wTo) {
                        if (i > wFrom && isChapterStart(allParas[i].content) && pageHasContent) break;
                        if (rectBottom(i) <= limitY) { i++; pageHasContent = true; continue; }
                        // 整段/图片放不下：空页硬放（防死循环），否则推下一页
                        if (!pageHasContent) { i++; }
                        break;
                    }
                    if (i >= wTo || i >= chunkEnd) continue;
                    if (wb[wb.length - 1].paraIndex === i) break; // 没推进，防死循环
                    wb.push({ paraIndex: i, offset: 0 });
                    wCursor = rectTop(i) - chapterTopGap(i);
                }
                if (wb.length > 1) {
                    provisionalShown = true;
                    provisionalRangeRef.current = { from: wFrom, to: wTo };
                    setPageBreaks(wb);
                    setTotalPages(Math.max(1, wb.length));
                    const tp = pageOf(wb, anchorPos0);
                    setPage(tp + 1);
                    repaginatingRef.current = false;
                    provisionalLandIdx = allParas[wb[tp].paraIndex]?.idx ?? null;
                    savedParaIdxRef.current = null; // 锚点已用掉，全书分页完成时按实时阅读位置重映射
                    savedParaOffsetRef.current = 0;
                    setReadingLoading(false); // 立即可读；全书分页下面照常跑
                }
            }

            let pi = 0, off = 0;
            mountChunk(0, null, null);
            chunkEnd = blocks.length;
            let cursorY = blocks.length ? rectTop(0) - chapterTopGap(0) : 0;
            if (progressive) setPaginateProgress(0);
            let guard = 0;
            while (pi < allParas.length && guard++ < 100000) {
                if (!(await ensureChunk(pi))) return;
                const limitY = cursorY + maxHeight + 1; // +1对齐旧算法的subpixel容差
                let pageHasContent = false;
                while (pi < chunkEnd && pi < allParas.length) {
                    const isImg = /^\[IMG:[^\]]+\]$/.test(allParas[pi].content);
                    if (off === 0 && pi > 0 && isChapterStart(allParas[pi].content) && pageHasContent) break;
                    if (rectBottom(pi) <= limitY) {
                        pi++; off = 0; pageHasContent = true; continue;
                    }
                    if (isImg) {
                        // 图片不可拆；单独成页也放不下就硬放（imgMaxH≤0.6页高，实际必放得下）
                        if (!pageHasContent) { pi++; off = 0; pageHasContent = true; }
                        break;
                    }
                    const cut = lineCut(pi, limitY);
                    if (cut <= off) break; // 一行都进不来，整段推下页
                    if (cut >= (innerText(pi)?.length ?? 0)) { // 文本全放下了（块底差subpixel）
                        pi++; off = 0; pageHasContent = true; continue;
                    }
                    // widow control: 段从头开始且只塞得下<4字且页内已有内容 → 整段推下页
                    if (off === 0 && cut < 4 && pageHasContent) break;
                    off = cut;
                    pageHasContent = true;
                    break;
                }
                if (pi >= allParas.length) break;
                if (pi >= chunkEnd) continue; // 块用完但页未填满：换块（ensureChunk）后继续填当前页
                const last = breaks[breaks.length - 1];
                if (last.paraIndex === pi && last.offset === off) break; // 没推进，防死循环
                breaks.push({ paraIndex: pi, offset: off });
                if (off > 0) {
                    // 段中切点：下一页顶=切点字符所在行的top
                    const ti = innerText(pi)!;
                    const [sn, sk] = locate(ti, off + 1);
                    measureRange.setStart(sn, Math.max(0, sk - 1));
                    measureRange.setEnd(sn, sk);
                    cursorY = measureRange.getBoundingClientRect().top + rectShift;
                } else {
                    cursorY = rectTop(pi) - chapterTopGap(pi);
                }
            }
            measurer.innerHTML = ''; // 测量节点用完即清

            if (cancelled) return;
            provisionalRangeRef.current = null;
            // 落页必须和新页表同批提交：下面写缓存有await，期间"旧页码+新页表"会先渲染一次，
            // 把currentParaIdxRef刷成无关段落。重排期间没翻过页就用开始时钉住的anchorPos0。
            if (!suppressPageJumpRef.current) {
                const live = currentParaIdxRef.current;
                const turned = live != null && live !== startLiveIdx && live !== provisionalLandIdx;
                const anchor = savedParaIdxRef.current != null
                    ? { idx: savedParaIdxRef.current, off: savedParaOffsetRef.current }
                    : turned ? (anchorPosRef.current ?? { idx: live!, off: 0 }) : anchorPos0;
                const targetPage = pageOf(breaks, anchor);
                setPage(Math.max(1, Math.min(breaks.length, targetPage + 1)));
            }
            repaginatingRef.current = false;
            setPageBreaks(breaks);
            setTotalPages(Math.max(1, breaks.length));
            syncPagination(breaks);
            if (paginationCacheKey) {
                const payload = JSON.stringify({
                    breaks, paraCount: allParas.length,
                    width: readerContentWidth, height: readerSize.height,
                });
                try {
                    for (let i = localStorage.length - 1; i >= 0; i--) {
                        const k = localStorage.key(i);
                        if (k && k.startsWith('pagebreaks-') && !k.startsWith('pagebreaks-v3-')) localStorage.removeItem(k);
                    }
                } catch {}
                const idbOk = await idbSet(paginationCacheKey, payload);
                if (cancelled) return;
                if (idbOk) {
                    try { localStorage.removeItem(paginationCacheKey); } catch {}
                } else {
                    try {
                        localStorage.setItem(paginationCacheKey, payload);
                    } catch {
                        try {
                            for (let i = localStorage.length - 1; i >= 0; i--) {
                                const k = localStorage.key(i);
                                if (k && k.startsWith('pagebreaks-v3-') && k !== paginationCacheKey) localStorage.removeItem(k);
                            }
                            localStorage.setItem(paginationCacheKey, payload);
                        } catch {}
                    }
                }
            }
            savedParaIdxRef.current = null;
            savedParaOffsetRef.current = 0;
            setPaginateProgress(null);
            setReadingLoading(false);
            if (provisionalShown) toast('全书分页已完成');
        };
        // 拨辅助阅读开关时等开关动画走完、连拨几下只重排最后一次
        const assistChanged = prevAssistSigRef.current !== null && prevAssistSigRef.current !== assistSig;
        prevAssistSigRef.current = assistSig;
        const startTimer = assistChanged ? setTimeout(run, 300) : null;
        if (!startTimer) run();
        return () => { cancelled = true; if (startTimer) clearTimeout(startTimer); };
    }, [mode, allParas, readerContentWidth, readerSize.height, readerFontSize, activeBook?.id, activeBook?.total_paragraphs, assistSig]);

    useEffect(() => {
        if (allParas.length === 0 || pageBreaks.length === 0) {
            setPageFragments([]);
            setPageFragmentsRight([]);
            setParagraphs([]);
            setComments([]);
            currentParaIdxRef.current = null;
            return;
        }
        if (page > pageBreaks.length && !suppressPageJumpRef.current) {
            setPage(pageBreaks.length);
            return;
        }
        // 双页时左页永远是奇数页，跳转落到偶数页就退一页，两页一起摊开
        if (spread && page % 2 === 0) { setPage(page - 1); return; }
        const buildFragments = (p: number): PageFragment[] => {
            const start = pageBreaks[p - 1] || { paraIndex: 0, offset: 0 };
            const end = p < pageBreaks.length ? pageBreaks[p] : { paraIndex: allParas.length, offset: 0 };
            const out: PageFragment[] = [];
            for (let i = start.paraIndex; i < end.paraIndex || (i === end.paraIndex && end.offset > 0); i++) {
                const para = allParas[i];
                if (!para) continue;
                const text = stripHeading(para.content);
                const from = i === start.paraIndex ? start.offset : 0;
                const to = i === end.paraIndex ? end.offset : text.length;
                if (to <= from) continue;
                out.push({ ...para, content: text.slice(from, to), sourceIdx: i, startOffset: from, endOffset: to, isPartialStart: from > 0, isPartialEnd: to < text.length });
            }
            return out;
        };
        const fragments = buildFragments(page);
        const right = spread && page < pageBreaks.length ? buildFragments(page + 1) : [];
        setPageFragments(fragments);
        setPageFragmentsRight(right);
        const visibleParas = [...fragments, ...right].map(f => allParas[f.sourceIdx]).filter((p, i, arr) => p && arr.indexOf(p) === i);
        setParagraphs(visibleParas);
        setComments(allComments);
        const firstFrag = fragments[0] ?? right[0];
        if (firstFrag) {
            // 锚点还在可见页（双页=左右两页）里就不动它；离开了才换成可见页首
            const lastShown = page + (spread ? 1 : 0);
            const end = lastShown < pageBreaks.length ? pageBreaks[lastShown] : { paraIndex: allParas.length, offset: 0 };
            const a = anchorPosRef.current;
            const anchorPi = a ? allParas.findIndex(p => p.idx === a.idx) : -1;
            const before = (pi: number, off: number, b: PageBreak) => pi < b.paraIndex || (pi === b.paraIndex && off < b.offset);
            const inView = anchorPi >= 0 && !before(anchorPi, a!.off, { paraIndex: firstFrag.sourceIdx, offset: firstFrag.startOffset }) && before(anchorPi, a!.off, end);
            if (!inView && !repaginatingRef.current) anchorPosRef.current = { idx: allParas[firstFrag.sourceIdx].idx, off: firstFrag.startOffset };
            currentParaIdxRef.current = anchorPosRef.current?.idx ?? allParas[firstFrag.sourceIdx].idx;
        } else {
            currentParaIdxRef.current = null;
        }
        // 进度存锚点：转屏后页首变了，读到的位置没变；新书分页落地前（savedParaIdxRef 还在）不存，免得把进度冲成第1页
        if (activeBook && visibleParas.length > 0 && savedParaIdxRef.current === null) {
            const pos = anchorPosRef.current ?? (firstFrag ? { idx: allParas[firstFrag.sourceIdx].idx, off: firstFrag.startOffset } : { idx: visibleParas[0].idx, off: 0 });
            api.updateBookProgress(activeBook.id, pos.idx, pos.off).catch(() => {});
        }
    }, [page, pageBreaks, allParas, allComments, activeBook?.id, spread]);

    const autoFinishRef = useRef<number | null>(null);
    const goPage = (delta: number) => {
        if (!activeBook) return;
        let next = Math.max(1, Math.min(totalPages, page + delta * (spread ? 2 : 1)));
        if (spread && next % 2 === 0) next -= 1;
        if (next !== page) {
            setActiveComments([]); setCommentingIdx(null); setSelRange(null); setFloatingBar(null);
            setPage(next);
            if (next + (spread ? 1 : 0) >= totalPages && totalPages > 1 && autoFinishRef.current !== activeBook.id) {
                autoFinishRef.current = activeBook.id;
                api.markBookFinished(activeBook.id, localDateString()).catch(() => {});
            }
        }
    };

    const stepLine = (delta: 1 | -1) => {
        const next = stepFocusIndex(focusLines, focusIdx, delta, focusUnit);
        if (next !== null) { setFocusIdx(next); return; }
        if (delta > 0 ? page + (spread ? 1 : 0) >= totalPages : page <= 1) return;
        focusLandRef.current = delta > 0 ? 'first' : 'last';
        goPage(delta);
    };
    const stepLineRef = useRef(stepLine);
    stepLineRef.current = stepLine;

    const focusPageKey = pageFragments.length ? `${pageFragments[0].sourceIdx}:${pageFragments[0].startOffset}-${pageFragments[pageFragments.length - 1].sourceIdx}:${pageFragments[pageFragments.length - 1].endOffset}` : '';
    useEffect(() => {
        const host = contentRef.current;
        if (!lineFocus || mode !== 'reading' || readingLoading || !host) { setFocusLines([]); return; }
        let raf1 = 0, raf2 = 0, cancelled = false;
        const measure = () => {
            cancelAnimationFrame(raf1); cancelAnimationFrame(raf2);
            raf1 = requestAnimationFrame(() => { raf2 = requestAnimationFrame(() => {
                if (cancelled) return;
                // 双页时左右两页分开量再接上：左页的行在前，右页段落编号接着左页往后排
                const lines: LineRect[] = [];
                for (const root of Array.from(host.querySelectorAll('[data-page-content]')) as HTMLElement[]) {
                    const base = lines.reduce((m, l) => Math.max(m, (l.block ?? -1) + 1), 0);
                    for (const l of measurePageLines(root, host, '[data-para-idx]')) lines.push(l.block !== undefined ? { ...l, block: l.block + base } : l);
                }
                const max = Math.max(0, lines.length - 1);
                const turned = focusPageKeyRef.current !== focusPageKey;
                const land = focusLandRef.current;
                if (turned) { focusPageKeyRef.current = focusPageKey; focusLandRef.current = 'first'; }
                setFocusLines(lines);
                setFocusIdx(prev => turned ? (land === 'last' ? max : 0) : Math.min(prev, max));
            }); });
        };
        measure();
        const imgs = Array.from(host.querySelectorAll('[data-page-content] img')).filter(i => !(i as HTMLImageElement).complete);
        imgs.forEach(i => i.addEventListener('load', measure));
        document.fonts?.ready.then(() => { if (!cancelled) measure(); });
        return () => { cancelled = true; cancelAnimationFrame(raf1); cancelAnimationFrame(raf2); imgs.forEach(i => i.removeEventListener('load', measure)); };
    }, [lineFocus, mode, readingLoading, focusPageKey, pageFragments, pageFragmentsRight, readerFontSize, readerNightMode, readerSize.width, readerSize.height, assistSig]);

    useEffect(() => {
        if (!lineFocus || mode !== 'reading') return;
        const onKey = (e: KeyboardEvent) => {
            if (e.altKey || e.ctrlKey || e.metaKey) return;
            const t = e.target as HTMLElement | null;
            if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.tagName === 'BUTTON' || t.isContentEditable)) return;
            const d = e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 0;
            if (!d) return;
            e.preventDefault();
            stepLineRef.current(d);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [lineFocus, mode]);

    const assistDemoRef = useRef<HTMLDivElement>(null);
    const [assistDemoLines, setAssistDemoLines] = useState<LineRect[]>([]);
    useLayoutEffect(() => {
        const box = assistDemoRef.current;
        if (assistPhase === 'closed' || !lineFocus || !box) { setAssistDemoLines([]); return; }
        const text = box.querySelector('[data-demo-text]') as HTMLElement | null;
        setAssistDemoLines(text ? measurePageLines(text, box, '[data-demo-text]') : []);
    }, [assistPhase, lineFocus, assistSig, readerFontSize, readerNightMode]);

    const handleContentClick = (e: React.MouseEvent<HTMLDivElement>) => {
        if (mode !== 'reading') {
            if (activeComments.length) setActiveComments([]);
            return;
        }

        // Touch browsers commonly emit a click after touchend. A completed swipe
        // already changed the page, so ignore that synthetic click.
        if (suppressTapRef.current) {
            suppressTapRef.current = false;
            if (suppressTapTimer.current) clearTimeout(suppressTapTimer.current);
            suppressTapTimer.current = null;
            return;
        }

        const target = e.target as HTMLElement;
        if (target.closest('button, a, input, textarea, select, [role="button"], [data-reader-interactive]')) return;

        // Finishing a mouse drag or long-press selection can also emit a click.
        // Keep annotation selection intact instead of accidentally turning a page.
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed && selection.toString().trim()) return;

        if (lineFocus) {
            if (activeComments.length > 0) { setActiveComments([]); return; }
            const r = e.currentTarget.getBoundingClientRect();
            const x = r.width > 0 ? (e.clientX - r.left) / r.width : 0.5;
            const y = r.height > 0 ? (e.clientY - r.top) / r.height : 0.5;
            if (x > 0.3 && x < 0.7 && y > 0.35 && y < 0.65) toggleBar();
            else stepLine(x >= 0.7 || (x > 0.3 && y >= 0.65) ? 1 : -1);
            return;
        }

        const rect = e.currentTarget.getBoundingClientRect();
        const tapPosition = rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0.5;
        if (pageTurnMode === 'tap') {
            if (tapPosition < 0.3) goPage(-1);
            else if (tapPosition > 0.7) goPage(1);
            else toggleBar();
        } else {
            toggleBar();
        }
    };

    const startAnnotation = () => {
        replyPageRef.current = page;
        if (!floatingBar) return;
        setSelRange({ startPara: floatingBar.startPara, endPara: floatingBar.endPara, start: floatingBar.start, end: floatingBar.end });
        setSelectedText(floatingBar.text);
        setCommentingIdx(floatingBar.startPara);
        setFloatingBar(null);
        window.getSelection()?.removeAllRanges();
    };

    const handleAddComment = async () => {
        if (!activeBook || commentingIdx === null || !commentText.trim() || commentSubmittingRef.current) return;
        commentSubmittingRef.current = true;
        try {
            const result = await api.addBookComment(activeBook.id, {
                paragraph_idx: commentingIdx, content: commentText.trim(), from_who: humanName,
                selected_text: selectedText || undefined,
                sel_start_idx: selRange ? selRange.start : undefined,
                sel_end_idx: selRange ? selRange.end : undefined,
                sel_end_para_idx: selRange && selRange.endPara !== selRange.startPara ? selRange.endPara : undefined,
                reply_to: replyingTo?.id || undefined,
            } as any);
            const newComment: Comment = {
                id: result?.id ?? Date.now(), book_id: activeBook.id, paragraph_idx: commentingIdx,
                sel_start_idx: selRange?.start ?? null, sel_end_idx: selRange?.end ?? null,
                sel_end_para_idx: selRange && selRange.endPara !== selRange.startPara ? selRange.endPara : null,
                selected_text: selectedText || null, from_who: humanName,
                content: commentText.trim(), created_at: new Date().toISOString(), reply_to: replyingTo?.id ?? null,
            };
            const pageToRestore = replyPageRef.current ?? page;
            replyPageRef.current = null;
            suppressPageJumpRef.current = true;
            setCommentText(''); setSelectedText(''); setSelRange(null); setReplyingTo(null);
            setActiveComments([]); setCommentingIdx(null);
            setComments(prev => [...prev, newComment]);
            const bookId = activeBook.id;
            setAllComments(prev => { const u = [...prev, newComment]; cacheComments(bookId, u); return u; });
            setPage(pageToRestore);
            setTimeout(() => { suppressPageJumpRef.current = false; }, 500);
        } catch (e: any) { toast(`批注失败: ${e.message}`); }
        finally { commentSubmittingRef.current = false; }
    };

    const handleDeleteComment = async (cmt: Comment) => {
        try {
            await api.deleteBookComment(cmt.id);
            setComments(prev => prev.filter(x => x.id !== cmt.id));
            const bookId = cmt.book_id;
            setAllComments(prev => { const u = prev.filter(x => x.id !== cmt.id); cacheComments(bookId, u); return u; });
            setActiveComments(prev => prev.filter(x => x.id !== cmt.id));
        } catch (e: any) { toast(`删除失败: ${e.message}`); }
    };

    const handleExport = () => {
        if (!activeBook) return;
        window.open(`${BASE}/v1/books/${activeBook.id}/export?format=epub`, '_blank');
    };

    const handleDeleteBook = async (bookId: number) => {
        try {
            await api.deleteBook(bookId);
            // 删书连缓存一起清（分页+段落+批注）
            try { localStorage.removeItem(`pagebreaks-v3-${bookId}`); } catch {}
            idbDel(`pagebreaks-v3-${bookId}`);
            idbDelParas(`paras-v1-${bookId}`);
            idbDelParas(`comments-v1-${bookId}`);
            setConfirmDelete(null); loadBooks();
            toast('已删除');
        } catch (e: any) { toast(`删除失败: ${e.message}`); }
    };

    const jumpToChapter = (chapter: { idx: number; page: number; title: string }) => {
        if (!activeBook) return;
        const targetIdx = chapter.idx ?? chapter.page;
        if (!canJumpToPara(targetIdx)) return;
        setTocPhase('closing'); setTimeout(() => { setShowToc(false); setTocPhase('closed'); }, 240);
        setActiveComments([]); setCommentingIdx(null); setSelRange(null); setFloatingBar(null);
        const targetPage = findPageForParaIdx(targetIdx);
        if (targetPage >= 0) setPage(targetPage + 1);
    };

    // 当前阅读位置所在章：最后一个起始页不超过当前页的章（目录要能定位当前章，不用从头划）
    const currentChapterIdx = useMemo(() => {
        let cur = -1;
        for (let i = 0; i < tocChapters.length; i++) {
            const ch = tocChapters[i];
            const pg = findPageForParaIdx(ch.idx ?? ch.page);
            if ((pg >= 0 ? pg + 1 : ch.page) <= page) cur = i;
            else break;
        }
        return cur;
    }, [tocChapters, page, pageBreaks, allParas]);

    // 打开目录时把当前章滚动到列表中央（窗口化后按钮按需渲染，不能scrollIntoView，直接算scrollTop）
    useEffect(() => {
        if (!showToc) return;
        const el = tocListRef.current;
        if (!el) return;
        setTocViewH(el.clientHeight);
        setTocScrollTop(el.scrollTop);
        if (currentChapterIdx >= 0) {
            const headerH = (el.firstElementChild as HTMLElement | null)?.offsetHeight ?? 0;
            el.scrollTop = Math.max(0, headerH + currentChapterIdx * TOC_ROW_H - el.clientHeight / 2);
        }
    }, [showToc, currentChapterIdx]);

    // 二进制直传：选文件只存File对象，编码探测（utf8/gbk）挪到服务端
    const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const name = file.name.replace(/\.(pdf|txt|md|epub)$/i, '');
        if (!uploadTitle) setUploadTitle(name);
        setUploadFileName(file.name);
        setUploadFile(file); setUploadText('');
    };

    const handleBatchUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files;
        if (!files || files.length === 0) return;
        setUploading(true);
        let ok = 0, fail = 0;
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const ext = file.name.toLowerCase().split('.').pop();
            if (!['epub', 'pdf', 'txt', 'md'].includes(ext || '')) { fail++; continue; }
            try {
                const title = file.name.replace(/\.(pdf|txt|md|epub)$/i, '');
                await api.uploadBookFile(file, title, ext || 'txt');
                ok++;
                toast(`已上传 ${ok}/${files.length}: ${title}`);
            } catch { fail++; }
        }
        toast(fail ? `完成：${ok}成功，${fail}失败` : `全部${ok}本上传成功`);
        setUploading(false);
        setShowUpload(false);
        loadBooks();
        e.target.value = '';
    };

    const handleUpload = async () => {
        if (!uploadTitle.trim()) { toast('请输入书名'); return; }
        if (!uploadText && !uploadFile) { toast('请选择文件或粘贴文本'); return; }
        setUploading(true);
        try {
            if (uploadFile) {
                const ext = uploadFileName.toLowerCase().split('.').pop() || 'txt';
                await api.uploadBookFile(uploadFile, uploadTitle.trim(), ext);
            } else {
                await api.createBook({ title: uploadTitle.trim(), content: uploadText });
            }
            setShowUpload(false); setUploadTitle(''); setUploadText(''); setUploadFile(null); setUploadFileName('');
            toast('上传成功');
            loadBooks();
        } catch (e: any) { toast(`上传失败: ${e.message}`); }
        setUploading(false);
    };

    const backToShelf = () => {
        ++openRequest.current;
        window.dispatchEvent(new Event('coread-pause'));
        setOitChatOpen(false);
        setMode('shelf'); setActiveBook(null); setParagraphs([]); setComments([]);
        setActiveComments([]); setSelRange(null); setFloatingBar(null); setShowToc(false); setTocChapters([]);
        setReturnPoint(null);
        loadBooks();
    };

    useEffect(() => {
        if ((mode !== 'reading' && mode !== 'shelf') || !READ_TOGETHER) return;
        let alive = true;
        const poll = async () => {
            try {
                const sessionId = oitChatSessionRef.current;
                if (!sessionId) return;
                await api.oitChat('heartbeat', { session_id: sessionId });
                const state: any = await api.oitChat('state', { session_id: sessionId });
                if (!alive) return;
                setOitChatMessages(state.messages || []);
                if (!state.active) {
                    const finalMessage = [...(state.messages || [])].reverse().find(message => message.role === 'ai');
                    toast(finalMessage?.text || state.end_reason || '共同阅读已结束');
                    oitChatSessionRef.current = '';
                    window.dispatchEvent(new Event('coread-pause'));
                    setOitChatOpen(false);
                    setMode('shelf'); setActiveBook(null);
                    setPage(1); setTotalPages(1); setParagraphs([]); setComments([]);
                    setPageFragments([]); setPageBreaks([{ paraIndex: 0, offset: 0 }]);
                }
                setOitChatStatus(state.active ? (state.busy ? '对方正在回复…' : '') : '阅读模式已结束，请返回 OIT 重新打开共读。');
            } catch (e: any) {
                if (alive && e.status === 409) {
                    oitChatSessionRef.current = '';
                    setOitChatMessages([]);
                    setOitChatOpen(false);
                    setMode('shelf'); setActiveBook(null);
                    toast(e.message || '共读已结束');
                } else if (alive) setOitChatStatus(`OIT 暂时无法连接: ${e.message}`);
            }
        };
        poll();
        const timer = window.setInterval(poll, 2200);
        return () => { alive = false; window.clearInterval(timer); };
    }, [mode]);

    useEffect(() => { oitChatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [oitChatMessages, oitChatOpen]);

    const sendOitChat = async () => {
        const text = oitChatInput.trim();
        if (!text || oitChatBusy) return;
        const visible = [...pageFragments, ...pageFragmentsRight];
        const context = [
            activeBook ? `书名: ${activeBook.title}` : '当前位于选书页面，协助讨论候选书目。',
            ...(activeBook ? [`当前页: ${page}${spread && page < totalPages ? `-${page + 1}` : ''} / ${totalPages}`] : []),
            ...visible.map(fragment => {
                const comments = commentsForPara(fragment.idx);
                return `[段落 ${fragment.idx}] ${fragment.content}` + comments.map(comment =>
                    `\n批注 (${comment.from_who}): ${comment.content}`).join('');
            }),
        ].join('\n\n').slice(0, 18000);
        setOitChatBusy(true);
        try {
            const result: any = await api.oitChat('send', { text, context, session_id: oitChatSessionRef.current });
            if (!result.ok) throw new Error(result.error || '发送失败');
            setOitChatInput('');
            setOitChatStatus('');
        } catch (e: any) { setOitChatStatus(e.message || '发送失败'); }
        finally { setOitChatBusy(false); }
    };

    const commentsForPara = (idx: number) => comments.filter(x => {
        if (x.sel_start_idx == null) return x.paragraph_idx === idx;
        const endPara = x.sel_end_para_idx ?? x.paragraph_idx;
        return x.paragraph_idx <= idx && idx <= endPara;
    });
    const stripHeading = (s: string) => s.replace(/^#+\s*/, '');
    const isHeading = (s: string) => s.trim().startsWith('#');
    const isChapterStart = (s: string) => {
        const trimmed = s.trim();
        const plain = stripHeading(trimmed).trim();
        const isChapter = /^(chapter|book|part|volume|prologue|epilogue)\b/i.test(plain)
            || /^\u7b2c[\d\s\w\u4e00-\u9fff]{1,20}[\u7ae0\u8282\u5377\u90e8\u7bc7\u56de]/.test(plain)
            || /^\u7b2c\d+\u7ae0/.test(trimmed);
        if (isChapter) return true;
        const heading = trimmed.match(/^(#{1,6})\s+/);
        return !!(heading && heading[1].length <= 2);
    };

    // 高亮区间切成不重叠片段（先到先得），普通渲染和辅助阅读渲染共用
    const highlightPieces = (text: string, highlights: Comment[]) => {
        const positioned = highlights
            .filter(h => h.sel_start_idx != null && h.sel_end_idx != null && h.sel_start_idx! < text.length)
            .sort((a, b) => a.sel_start_idx! - b.sel_start_idx!);
        const pieces: { from: number; to: number; h: Comment }[] = [];
        let lastEnd = 0;
        for (const h of positioned) {
            const start = Math.max(h.sel_start_idx!, lastEnd);
            const end = Math.min(h.sel_end_idx!, text.length);
            if (start >= end) continue;
            pieces.push({ from: start, to: end, h });
            lastEnd = end;
        }
        return { positioned, pieces };
    };

    const highlightSpan = (key: string, h: Comment, paraIdx: number, positioned: Comment[], textLen: number, hStart: number, hEnd: number, showDot: boolean, children: React.ReactNode) => {
        const isShen = h.from_who.toLowerCase() === 'ai' || h.from_who.toLowerCase() === aiName.toLowerCase();
        const hlBg = isShen ? c.shenHL : c.tongHL;
        const dotColor = isShen ? c.shenColor : c.tongColor;
        return (
            <span key={key}
                onClick={(e) => {
                    if (window.getSelection()?.toString().trim()) return;
                    e.stopPropagation();
                    const overlapping = positioned.filter(x => {
                        const xs = Math.max(x.sel_start_idx!, 0), xe = Math.min(x.sel_end_idx!, textLen);
                        return xs < hEnd && xe > hStart;
                    });
                    const allReplies: Comment[] = [];
                    const findReplies = (ids: number[]) => { const found = comments.filter(r => r.reply_to && ids.includes(r.reply_to)); if (found.length) { allReplies.push(...found); findReplies(found.map(f => f.id)); } };
                    findReplies(overlapping.map(o => o.id));
                    const withReplies = [...new Map([...overlapping, ...allReplies].map(x => [x.id, x])).values()];
                    setActiveComments(prev => prev.length > 0 && prev[0]?.id === overlapping[0]?.id ? [] : withReplies);
                }}
                style={{
                    backgroundImage: `linear-gradient(${hlBg}, ${hlBg})`,
                    backgroundRepeat: 'no-repeat',
                    backgroundSize: '100% 62%',
                    backgroundPosition: '0 72%',
                    borderRadius: 3,
                    position: 'relative',
                    cursor: 'pointer',
                    textDecorationLine: 'none',
                    padding: 0,
                    lineHeight: 'inherit',
                    boxDecorationBreak: 'clone',
                    WebkitBoxDecorationBreak: 'clone',
                } as React.CSSProperties}>
                {showDot && <span style={{ position: 'absolute', top: -2, left: -2, width: 7, height: 7, borderRadius: '50%', background: dotColor, boxShadow: `0 0 3px ${dotColor}60`, pointerEvents: 'none' }} />}
                {children}
            </span>
        );
    };

    const renderHighlighted = (text: string, paraIdx: number, highlights: Comment[]) => {
        const { positioned, pieces } = highlightPieces(text, highlights);
        if (pieces.length === 0) return text;
        const parts: React.ReactNode[] = [];
        let lastEnd = 0;
        for (const { from, to, h } of pieces) {
            if (from > lastEnd) parts.push(<React.Fragment key={`t${paraIdx}-${lastEnd}`}>{text.slice(lastEnd, from)}</React.Fragment>);
            parts.push(highlightSpan(`h${h.id}-${paraIdx}`, h, paraIdx, positioned, text.length, from, to, h.paragraph_idx === paraIdx, text.slice(from, to)));
            lastEnd = to;
        }
        if (lastEnd < text.length) parts.push(<React.Fragment key={`t${paraIdx}-${lastEnd}`}>{text.slice(lastEnd)}</React.Fragment>);
        return <>{parts}</>;
    };

    // 辅助阅读渲染：外层是词 span（结构与 appendAssistNodes 一致），高亮切进词里面，空隙只用 margin，不往文本里加字符
    const renderAssistText = (fullText: string, fragStart: number, fragEnd: number, fs: number, paraIdx: number, highlights: Comment[]) => {
        const text = fullText.slice(fragStart, fragEnd);
        const { positioned, pieces } = highlightPieces(text, highlights);
        const dotShown = new Set<number>();
        const out: React.ReactNode[] = [];
        for (const t of placeTokens(tokensFor(fullText), fragStart, fragEnd)) {
            const ts = t.start - fragStart, te = ts + t.text.length;
            const inner: React.ReactNode[] = [];
            let cur = ts;
            for (const p of pieces) {
                if (p.to <= cur || p.from >= te) continue;
                const a = Math.max(p.from, cur), b = Math.min(p.to, te);
                if (a > cur) inner.push(text.slice(cur, a));
                const first = !dotShown.has(p.h.id);
                dotShown.add(p.h.id);
                inner.push(highlightSpan(`h${p.h.id}-${paraIdx}-${a}`, p.h, paraIdx, positioned, text.length, p.from, p.to, first && p.h.paragraph_idx === paraIdx, text.slice(a, b)));
                cur = b;
            }
            if (cur < te) inner.push(text.slice(cur, te));
            const st = tokenStyle(t.kind, t.text, assistOpts, fs * 0.3, t.gapAfter, readerNightMode);
            const children = inner.length === 1 && typeof inner[0] === 'string' ? inner[0] : inner;
            out.push(st ? <span key={ts} style={st as React.CSSProperties}>{children}</span> : <React.Fragment key={ts}>{children}</React.Fragment>);
        }
        return out;
    };

    const glassBtn: React.CSSProperties = {
        width: 38, height: 38, borderRadius: '50%', padding: 0,
        background: 'rgba(255,255,255,0.5)',
        backdropFilter: 'blur(22px) saturate(1.8)', WebkitBackdropFilter: 'blur(22px) saturate(1.8)' as any,
        border: '1px solid rgba(255,255,255,0.75)',
        boxShadow: '0 2px 10px rgba(60,55,45,0.10), inset 0 1px 0 rgba(255,255,255,0.95), inset 0 -1px 0 rgba(255,255,255,0.3)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: INK,
    };
    const glassPop: React.CSSProperties = {
        position: 'absolute', right: 20, zIndex: 20, overflow: 'hidden', boxSizing: 'border-box',
        borderRadius: 18, padding: 5,
        background: 'linear-gradient(rgba(255,255,255,0.20), rgba(255,255,255,0) 40%), rgba(252,252,252,0.50)',
        backdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))', WebkitBackdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))' as any,
        boxShadow: 'var(--hyalite-edge, 0 0 0 0 transparent), 0 16px 50px rgba(60,55,45,0.14), inset 0 1px 0 rgba(255,255,255,0.95), inset 0 -1px 0 rgba(255,255,255,0.28)',
        border: '1px solid rgba(255,255,255,0.7)',
    };
    const vdHead: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 6, padding: '8px 11px 6px', cursor: 'pointer', color: INK };
    const vdInput: React.CSSProperties = { display: 'block', width: 'calc(100% - 22px)', margin: '2px 11px 8px', padding: '8px 11px', borderRadius: 11, border: '1px solid hsla(245,15%,55%,0.25)', background: 'rgba(255,255,255,0.55)', fontSize: 13, color: INK, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box' };
    const vdFile: React.CSSProperties = { display: 'block', width: 'calc(100% - 22px)', margin: '0 11px 8px', padding: '9px 11px', borderRadius: 11, border: '1px dashed hsla(245,15%,55%,0.4)', background: 'rgba(255,255,255,0.35)', fontSize: 12, color: INK2, textAlign: 'center', lineHeight: 1.5, whiteSpace: 'normal', overflowWrap: 'anywhere', boxSizing: 'border-box', cursor: 'pointer' };
    const vdCta: React.CSSProperties = { display: 'block', width: 'calc(100% - 22px)', margin: '0 11px 6px', padding: '9px 0', borderRadius: 12, border: 'none', background: INK, color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer' };
    const glassConfirm = ({ title, desc, confirmText, onConfirm, onCancel }: { title: string; desc?: string; confirmText: string; onConfirm: () => void; onCancel: () => void }) => (
        <>
            <div onClick={onCancel} style={{ position: 'absolute', inset: 0, zIndex: 30, background: 'rgba(30,26,20,0.24)', backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)' as any }} />
            <div className="hy-shelf cr-rise-in" onClick={(e) => e.stopPropagation()} style={{
                position: 'absolute', left: 12, right: 12, top: 'calc(92px + env(safe-area-inset-top))', zIndex: 31, boxSizing: 'border-box',
                borderRadius: 24, padding: '18px 18px 14px', textAlign: 'center',
                background: 'linear-gradient(rgba(255,255,255,0.26), rgba(255,255,255,0) 42%), rgba(252,252,252,0.62)',
                backdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))', WebkitBackdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))' as any,
                boxShadow: 'var(--hyalite-edge, 0 0 0 0 transparent), 0 18px 52px rgba(60,55,45,0.18), inset 0 1px 0 rgba(255,255,255,0.96)',
                border: '1px solid rgba(255,255,255,0.82)',
            }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: INK, letterSpacing: '-0.01em' }}>{title}</div>
                {desc && <div style={{ fontSize: 12, color: INK2, marginTop: 6, lineHeight: 1.6 }}>{desc}</div>}
                <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
                    <button onClick={onCancel} style={{ flex: 1, padding: '10px 0', borderRadius: 14, border: '1px solid rgba(60,55,45,0.15)', background: 'rgba(255,255,255,0.55)', fontSize: 13, color: INK2, cursor: 'pointer' }}>取消</button>
                    <button onClick={onConfirm} style={{ flex: 1, padding: '10px 0', borderRadius: 14, border: 'none', background: INK, fontSize: 13, color: '#fff', fontWeight: 600, cursor: 'pointer' }}>{confirmText}</button>
                </div>
            </div>
        </>
    );

    const renderPageFragments = (frags: PageFragment[]) => frags.map((frag, visibleIndex) => {
                                    const original = allParas[frag.sourceIdx] || frag;
                                    const heading = isHeading(original.content) && !frag.isPartialStart;
                                    const chapterTitle = isChapterStart(original.content) && !frag.isPartialStart;
                                    const rawInline = commentsForPara(frag.idx).filter(x => x.sel_start_idx != null && x.sel_end_idx != null);
                                    const inlineComments = rawInline.map(h => {
                                        const endPara = h.sel_end_para_idx ?? h.paragraph_idx;
                                        let s = h.sel_start_idx!, e = h.sel_end_idx!;
                                        if (h.paragraph_idx === frag.idx && endPara === frag.idx) { /* single para */ }
                                        else if (h.paragraph_idx === frag.idx) { e = frag.endOffset; }
                                        else if (endPara === frag.idx) { s = frag.startOffset; }
                                        else { s = frag.startOffset; e = frag.endOffset; }
                                        return { ...h, sel_start_idx: s - frag.startOffset, sel_end_idx: e - frag.startOffset };
                                    }).filter(h => h.sel_end_idx! > 0 && h.sel_start_idx! < frag.content.length);

                                    const blockComments = commentsForPara(frag.idx).filter(x => (x.sel_start_idx == null || x.sel_end_idx == null) && x.paragraph_idx === frag.idx && !frag.isPartialStart && !x.reply_to);

                                    const imgMatch = frag.content.match(/^\[IMG:([^\]]+)\]$/);
                                    if (imgMatch && activeBook) {
                                        const imgUrl = api.imageUrl(activeBook.id, imgMatch[1]);
                                        return (
                                            <div key={`${frag.idx}-${frag.startOffset}-${frag.endOffset}`} style={{ marginBottom: PARA_GAP, textAlign: 'center' }}>
                                                <img src={imgUrl} alt="" style={{ maxWidth: '100%', maxHeight: `${Math.floor(readerSize.height * 0.6)}px`, objectFit: 'contain', display: 'block', margin: '0 auto', borderRadius: 8 }} />
                                            </div>
                                        );
                                    }
                                    const paraFs = chapterTitle ? readerFontSize + 4 : original.content.trim().startsWith('# ') ? readerFontSize + 3 : original.content.trim().startsWith('## ') ? readerFontSize + 2 : readerFontSize;
                                    const paraSp = assistSpacing(chapterTitle ? 2.2 : 1.85, chapterTitle ? 1 : 0.3, paraFs, assistSpacingLv);
                                    const paraText = stripHeading(original.content);
                                    return (
                                        <div key={`${frag.idx}-${frag.startOffset}-${frag.endOffset}`} style={{ position: 'relative', marginBottom: chapterTitle ? CHAPTER_GAP_BOTTOM : PARA_GAP, marginTop: chapterTitle && visibleIndex > 0 ? CHAPTER_GAP_TOP : 0 }}>
                                            <div data-para-idx={frag.idx} data-frag-start={frag.startOffset} data-frag-end={frag.endOffset} style={{
                                                fontSize: paraFs,
                                                lineHeight: paraSp.lineHeight, color: readerNightMode ? (heading ? '#ddd' : '#ccc') : (heading ? '#222' : '#333'),
                                                letterSpacing: paraSp.letterSpacing, textIndent: (heading || chapterTitle || frag.isPartialStart) ? 0 : '1.5em',
                                                fontWeight: chapterTitle ? 800 : heading ? 700 : 400, marginBottom: heading ? 4 : 0,
                                                textAlign: chapterTitle ? 'center' : undefined,
                                                userSelect: 'text', WebkitUserSelect: 'text', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word',
                                            } as any}>
                                                {frag.content && assistFor(paraText)
                                                    ? renderAssistText(paraText, frag.startOffset, frag.endOffset, paraFs, frag.idx, inlineComments)
                                                    : renderHighlighted(decodeEntities(frag.content), frag.idx, inlineComments)}
                                            </div>

                                            {blockComments.length > 0 && (
                                                <div style={{ position: 'absolute', left: -16, top: (readerFontSize * assistSpacing(1.85, 0.3, readerFontSize, assistSpacingLv).lineHeight - 8) / 2, display: 'flex', flexDirection: 'column', gap: 4 }}>
                                                    {blockComments.map(cmt => {
                                                        const isShen = cmt.from_who.toLowerCase() === 'ai' || cmt.from_who.toLowerCase() === aiName.toLowerCase();
                                                        const color = isShen ? c.shenColor : c.tongColor;
                                                        return (
                                                            <span key={cmt.id} onClick={(e) => { e.stopPropagation(); const allR: Comment[] = []; const findR = (ids: number[]) => { const f = comments.filter(r => r.reply_to && ids.includes(r.reply_to)); if (f.length) { allR.push(...f); findR(f.map(x => x.id)); } }; findR([cmt.id]); setActiveComments(prev => prev.length > 0 && prev[0]?.id === cmt.id ? [] : [cmt, ...allR]); }}
                                                                style={{ width: 8, height: 8, borderRadius: '50%', background: color, cursor: 'pointer', display: 'inline-block', opacity: 0.7 }} />
                                                        );
                                                    })}
                                                </div>
                                            )}
                                        </div>
                                    );
    });

    return (
        <div className="xiaowo-study coread-study" style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', background: mode === 'reading' ? (readerNightMode ? '#1a1a1a' : '#fffcf3') : 'linear-gradient(145deg, #fbf9f6, #f5f1ee)', position: 'relative', overflow: 'hidden', filter: mode === 'reading' && readerBrightness < 100 ? `brightness(${readerBrightness / 100})` : undefined }}>
            <style>{`${STUDY_THEME_CSS}\n${COREAD_POP_CSS}\n@keyframes pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.05); } }`}</style>
            {READ_TOGETHER && (mode === 'reading' || mode === 'shelf') && (
                <button onClick={() => setOitChatOpen(value => !value)} style={{ position: 'absolute', right: 68, bottom: 30, zIndex: 18, border: '1px solid rgba(60,55,45,.14)', borderRadius: 22, padding: '10px 14px', cursor: 'pointer', color: readerNightMode ? '#ddd' : INK, background: readerNightMode ? 'rgba(40,40,40,.8)' : 'rgba(255,255,255,.85)', boxShadow: '0 2px 12px rgba(30,26,20,.12)' }}>
                    {oitChatOpen ? '收起聊天' : '💬 聊天'}
                </button>
            )}
            {READ_TOGETHER && (mode === 'reading' || mode === 'shelf') && oitChatOpen && (
                <section onClick={e => e.stopPropagation()} style={{ position: 'absolute', zIndex: 40, right: 12, top: 'calc(48px + env(safe-area-inset-top))', bottom: 84, width: 'min(390px, calc(100% - 24px))', display: 'flex', flexDirection: 'column', borderRadius: 18, overflow: 'hidden', color: readerNightMode ? '#eee' : INK, background: readerNightMode ? 'rgba(27,27,27,.96)' : 'rgba(255,255,255,.96)', border: '1px solid rgba(100,90,70,.16)', boxShadow: '0 10px 35px rgba(0,0,0,.2)' }}>
                    <header style={{ padding: '12px 14px', fontWeight: 650, borderBottom: '1px solid rgba(100,90,70,.14)' }}>
                        OIT · {oitChatMessages.length ? '共同阅读' : '沟通'}
                        <button onClick={() => setOitChatOpen(false)} style={{ float: 'right', border: 0, background: 'transparent', color: 'inherit', cursor: 'pointer' }}>×</button>
                    </header>
                    <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {oitChatMessages.map((message: any) => (
                            <div key={message.id} style={{ alignSelf: message.role === 'me' ? 'flex-end' : 'flex-start', maxWidth: '88%', padding: '8px 10px', borderRadius: 12, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', background: message.role === 'me' ? (readerNightMode ? '#39434a' : '#e9f2f7') : (readerNightMode ? '#30302d' : '#f3f0eb'), fontSize: 13, lineHeight: 1.55 }}>{message.text}</div>
                        ))}
                        <div ref={oitChatEndRef} />
                    </div>
                    {oitChatStatus && <div style={{ padding: '4px 12px', color: '#a44', fontSize: 11 }}>{oitChatStatus}</div>}
                    <div style={{ display: 'flex', gap: 8, padding: 10, borderTop: '1px solid rgba(100,90,70,.14)' }}>
                        <textarea value={oitChatInput} onChange={event => setOitChatInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendOitChat(); } }} placeholder="和当前聊天对象讨论这一页…" rows={2} style={{ flex: 1, resize: 'none', borderRadius: 10, padding: 8, border: '1px solid rgba(100,90,70,.22)', color: 'inherit', background: 'transparent', font: 'inherit', fontSize: 13 }} />
                        <button onClick={sendOitChat} disabled={oitChatBusy || !oitChatInput.trim()} style={{ alignSelf: 'flex-end', border: 0, borderRadius: 10, padding: '9px 12px', color: 'white', background: oitChatBusy ? '#999' : INK, cursor: oitChatBusy ? 'wait' : 'pointer' }}>发送</button>
                    </div>
                </section>
            )}
            {mode !== 'reading' && <>
                <div style={{ position: 'absolute', top: -70, right: -40, width: 210, height: 210, borderRadius: '50%', background: 'radial-gradient(circle, rgba(240,234,224,0.35), transparent 68%)', pointerEvents: 'none', filter: 'blur(12px)', opacity: 0.7 }} />
                <div style={{ position: 'absolute', bottom: 70, left: -70, width: 200, height: 200, borderRadius: '50%', background: 'radial-gradient(circle, rgba(245,241,238,0.4), transparent 68%)', pointerEvents: 'none', filter: 'blur(12px)', opacity: 0.65 }} />
            </>}

            {/* 阅读钟计时已移到顶栏书名下方 */}
            {/* Header — shelf always shows; reading mode header slides with toolbar */}
            {mode === 'shelf' ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, paddingTop: 'calc(52px + env(safe-area-inset-top))', paddingLeft: 20, paddingRight: 20, paddingBottom: 12, flexShrink: 0 }}>
                    <button onClick={() => window.history.back()} style={glassBtn}>
                        <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><path d="M10.5 3.5L5.5 8.5L10.5 13.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/></svg>
                    </button>
                    <span style={{ fontSize: 17, fontWeight: 600, color: INK, flex: 1, letterSpacing: '-0.01em' }}>共读室</span>
                    {editMode && selectedBooks.size > 0 && (
                        <button onClick={() => {
                            if (manageAction === 'delete') { setConfirmBatchDelete(true); return; }
                        }} style={{ ...glassBtn, width: 'auto', borderRadius: 19, padding: '0 14px', background: INK, border: 'none' }}>
                            <span style={{ fontSize: 12, color: 'white', fontWeight: 600 }}>删除{selectedBooks.size}</span>
                        </button>
                    )}
                    {editMode ? (
                        <button onClick={() => { setEditMode(false); setSelectedBooks(new Set()); setManageAction(null); }} style={{ ...glassBtn, width: 'auto', borderRadius: 19, padding: '0 14px' }}>
                            <span style={{ fontSize: 12, color: INK2, fontWeight: 600 }}>取消</span>
                        </button>
                    ) : (
                        <button onClick={() => setShowMenu(true)}
                            className={menuPhase === 'anim' ? 'cr-dots-out' : menuPhase === 'closing' ? 'cr-dots-in' : ''}
                            style={{ ...glassBtn, visibility: menuPhase === 'open' ? 'hidden' : undefined }}>
                            <svg width="17" height="17" viewBox="0 0 17 17" fill="currentColor"><circle cx="3.5" cy="8.5" r="1.5"/><circle cx="8.5" cy="8.5" r="1.5"/><circle cx="13.5" cy="8.5" r="1.5"/></svg>
                        </button>
                    )}
                    <button onClick={() => setShowUpload(true)} style={glassBtn}>
                        <span style={{ fontSize: 20, fontWeight: 300, lineHeight: 1, transform: 'translateY(-1px)' }}>+</span>
                    </button>
                </div>
            ) : (
                <>
                    {/* Persistent book title — always visible, small grey text */}
                    <div style={{
                        paddingTop: 'calc(12px + env(safe-area-inset-top))', paddingLeft: 20, paddingRight: 20, paddingBottom: 6, textAlign: 'center', flexShrink: 0,
                        background: readerNightMode ? '#1a1a1a' : '#fffcf3',
                    }}>
                        <div style={{ fontSize: 11, color: readerNightMode ? '#666' : '#aaa', letterSpacing: 0.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {activeBook?.title || ''}
                        </div>
                        <div style={{ fontSize: 11, color: readerNightMode ? '#666' : '#aaa', letterSpacing: 0.5, marginTop: 2, fontVariantNumeric: 'tabular-nums', visibility: showSessionClock ? 'visible' : 'hidden', opacity: showSessionClock ? 1 : 0, transition: 'opacity 160ms ease-out, visibility 160ms ease-out' }}>
                            本次阅读 {(() => { const s = Math.max(0, Math.floor(readingClock.seconds)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60; return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${m}:${String(ss).padStart(2, '0')}`; })()}
                        </div>
                    </div>
                    {/* 合上回书架 ×：常显在阅读页右上角 */}
                    <button onClick={backToShelf} style={{
                        position: 'absolute', top: 'calc(8px + env(safe-area-inset-top))', right: 12, zIndex: 15,
                        width: 28, height: 28, borderRadius: '50%', border: 'none', cursor: 'pointer',
                        background: readerNightMode ? 'rgba(255,255,255,0.10)' : 'rgba(60,55,45,0.08)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        color: readerNightMode ? '#999' : '#8a857b',
                    }}>
                        <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
                    </button>
                </>
            )}

            {/* Content */}
            <div ref={contentRef} data-testid="reader-surface" data-display-page={page} style={{
                flex: 1, overflow: mode === 'reading' ? 'hidden' : 'auto', position: 'relative',
                padding: mode === 'reading' ? '0' : '8px 20px 32px',
                background: mode === 'reading' ? (readerNightMode ? '#1a1a1a' : '#fffcf3') : 'transparent',
                touchAction: mode === 'reading' ? 'pan-y' : undefined,
                overscrollBehaviorX: mode === 'reading' ? 'none' : undefined,
                WebkitTapHighlightColor: mode === 'reading' ? 'transparent' : undefined,
            }} className="no-scrollbar study-scroll-container"
                onClick={handleContentClick}
                onTouchStart={mode === 'reading' ? (e) => {
                    touchStart.current = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() };
                } : undefined}
                onTouchEnd={mode === 'reading' ? (e) => {
                    if (!touchStart.current) return;
                    const dx = e.changedTouches[0].clientX - touchStart.current.x;
                    const dy = e.changedTouches[0].clientY - touchStart.current.y;
                    const dt = Date.now() - touchStart.current.t;
                    touchStart.current = null;
                    if (dt > 500 || Math.abs(dy) > Math.abs(dx) || Math.abs(dx) < 60) return;
                    suppressTapRef.current = true;
                    if (suppressTapTimer.current) clearTimeout(suppressTapTimer.current);
                    suppressTapTimer.current = setTimeout(() => {
                        suppressTapRef.current = false;
                        suppressTapTimer.current = null;
                    }, 500);
                    if (dx < -60) goPage(1);
                    else if (dx > 60) goPage(-1);
                } : undefined}>

                {loading ? (
                    <div style={{ textAlign: 'center', padding: '60px 0', color: '#bbb', fontSize: 14 }}>加载中...</div>
                ) : error ? (
                    <div style={{ textAlign: 'center', padding: '60px 20px' }}>
                        <div style={{ fontSize: 13, color: '#e88', marginBottom: 12 }}>{error}</div>
                        <button onClick={() => loadBooks()} style={{ background: 'none', border: '1px solid hsla(40,8%,16%,0.18)', borderRadius: 12, padding: '8px 20px', fontSize: 12, color: INK, cursor: 'pointer' }}>重试</button>
                    </div>
                ) : mode === 'shelf' ? (
                    <>
                        {doorLocked && (
                            <div style={{ margin: '0 20px 8px', padding: '8px 14px', borderRadius: 12, background: '#fff7ed', border: '1px solid #fed7aa', fontSize: 12, color: '#c2410c', textAlign: 'center', flexShrink: 0 }}>
                                🔒 共读室已关门 — {aiName}进不来，去 ⋯ 菜单开门
                            </div>
                        )}
                        {books.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: '60px 20px', color: '#bbb' }}>
                                <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'linear-gradient(135deg, rgba(240,234,224,0.5), rgba(245,241,238,0.6))', margin: '0 auto 16px' }} />
                                <div style={{ fontSize: 14, marginBottom: 6 }}>书架空空的</div>
                                <div style={{ fontSize: 12, color: '#ccc' }}>点右上角 + 上传一本书</div>
                            </div>
                        ) : (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, 100px)', justifyContent: 'space-between', columnGap: 8, rowGap: 12 }}>
                                {[...books].sort((a, b) => {
                                    const act = (x: any) => Math.max(
                                        x.last_opened_at ? new Date(x.last_opened_at).getTime() : 0,
                                        x.created_at ? new Date(x.created_at).getTime() : 0,
                                    );
                                    const d = act(b) - act(a);
                                    return d !== 0 ? d : b.id - a.id;
                                }).map((book, i) => {
                                    const progress = book.current_display_page && book.total_pages
                                        ? Math.round((book.current_display_page / book.total_pages) * 100) : 0;
                                    return (
                                        <div key={book.id} className="cr-shelf-in" style={{ position: 'relative', animationDelay: `${Math.min(i, 11) * 26}ms` }}>
                                            <button onClick={() => {
                                                if (editMode) {
                                                    setSelectedBooks(prev => { const s = new Set(prev); s.has(book.id) ? s.delete(book.id) : s.add(book.id); return s; });
                                                } else openBook(book);
                                            }} style={{
                                                background: 'none', padding: 0, border: 'none', cursor: 'pointer',
                                                textAlign: 'left', display: 'flex', flexDirection: 'column', width: '100%',
                                            }}>
                                                <div style={{ width: '100%', aspectRatio: '2/3', borderRadius: '4px 12px 12px 4px', overflow: 'hidden', position: 'relative', background: book.cover_image ? '#f0ebe3' : BOOK_COVERS[i % BOOK_COVERS.length], display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: '3px 3px 12px rgba(0,0,0,0.18), inset -2px 0 4px rgba(0,0,0,0.05)', borderLeft: `4px solid ${book.cover_image ? 'rgba(0,0,0,0.12)' : 'rgba(0,0,0,0.08)'}`, opacity: editMode && selectedBooks.has(book.id) ? 0.6 : 1 }}>
                                                    {editMode && (
                                                        <div style={{ position: 'absolute', top: 6, left: 8, width: 22, height: 22, borderRadius: '50%', background: selectedBooks.has(book.id) ? INK : 'rgba(255,255,255,0.7)', border: `2px solid ${selectedBooks.has(book.id) ? INK : 'rgba(0,0,0,0.2)'}`, zIndex: 2, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                                            {selectedBooks.has(book.id) && <span style={{ color: 'white', fontSize: 14, fontWeight: 700 }}>✓</span>}
                                                        </div>
                                                    )}
                                                    {book.cover_image ? (
                                                        <img src={api.imageUrl(book.id, book.cover_image, { thumb: true })} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                                    ) : (
                                                        <span style={{ fontSize: 22, fontWeight: 800, color: 'rgba(60,55,48,0.35)', padding: 8, textAlign: 'center', lineHeight: 1.3, wordBreak: 'break-all' }}>{book.title.slice(0, 4)}</span>
                                                    )}
                                                    {book.comment_count > 0 && (
                                                        <div style={{ position: 'absolute', top: 6, right: 6, background: 'rgba(255,255,255,0.8)', borderRadius: 8, padding: '1px 6px', fontSize: 9, fontWeight: 700, color: INK2 }}>
                                                            {book.comment_count}
                                                        </div>
                                                    )}
                                                </div>
                                                <div style={{ padding: '8px 2px 0', overflow: 'hidden' }}>
                                                    <div style={{ fontSize: 11, fontWeight: 600, color: INK, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' } as any}>{book.title}</div>
                                                    {progress > 0 && (
                                                        <div style={{ padding: '2px 2px 0', fontSize: 10, fontWeight: 500, color: INK2, letterSpacing: '0.01em' }}>已读 {Math.min(progress, 100)}%</div>
                                                    )}
                                                </div>
                                            </button>
                                            {!editMode && (
                                                <button onClick={(e) => { e.stopPropagation(); setConfirmDelete(book.id); }}
                                                    style={{ position: 'absolute', top: 4, left: 8, width: 20, height: 20, borderRadius: '50%', background: 'rgba(0,0,0,0.4)', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                                    <span style={{ color: 'white', fontSize: 12, lineHeight: 1 }}>×</span>
                                                </button>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </>
                ) : (
                    /* Reading Mode — immersive, no card border */
                    <>
                        {readingLoading ? (
                            <div style={{ textAlign: 'center', padding: '40px 20px', color: '#bbb', fontSize: 14 }}>
                                {paginateProgress != null ? (
                                    <>
                                        <div style={{ marginBottom: 12 }}>大书首次打开需要分页一次，之后秒开</div>
                                        <div style={{ width: 180, height: 4, borderRadius: 2, background: 'hsla(40,8%,16%,0.09)', margin: '0 auto 8px', overflow: 'hidden' }}>
                                            <div style={{ height: '100%', borderRadius: 2, width: `${Math.round(paginateProgress * 100)}%`, background: INK, transition: 'width 0.2s ease' }} />
                                        </div>
                                        <div style={{ fontSize: 12, color: '#ccc' }}>{Math.round(paginateProgress * 100)}%</div>
                                    </>
                                ) : '加载中...'}
                            </div>
                        ) : allParas.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: '40px 0', color: '#bbb', fontSize: 14 }}>这一页没有内容</div>
                        ) : (
                            spread ? (
                                <div style={{ display: 'flex', justifyContent: 'center', position: 'relative', zIndex: 1 }}>
                                    {/* 中缝细线：贴在双页容器正中，不占宽度、不改边距 */}
                                    <div aria-hidden style={{ position: 'absolute', left: '50%', top: 56, bottom: 24, width: 1, background: readerNightMode ? 'rgba(255,255,255,0.06)' : 'rgba(60,55,45,0.08)', pointerEvents: 'none' }} />
                                    {[pageFragments, pageFragmentsRight].map((frags, i) => (
                                        <div key={i} data-page-content style={{
                                            padding: i === 0 ? SPREAD_LEFT_PADDING : SPREAD_RIGHT_PADDING,
                                            width: readerContentWidth + READER_HORIZONTAL_PADDING + SPREAD_GUTTER / 2,
                                            flexShrink: 0, minHeight: pageHeight || undefined, boxSizing: 'border-box', overflow: 'hidden', position: 'relative',
                                        }}>
                                            {renderPageFragments(frags)}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                            <div data-page-content style={{ padding: READER_PAGE_PADDING, width: readerContentWidth + READER_HORIZONTAL_PADDING, maxWidth: '100%', margin: '0 auto', minHeight: pageHeight || undefined, boxSizing: 'border-box', overflow: 'hidden', position: 'relative', zIndex: 1 }}>
                                {renderPageFragments(pageFragments)}
                            </div>
                            )
                        )}
                    </>
                )}
                {mode === 'reading' && (
                    <div ref={measureRef} aria-hidden style={{
                        position: 'absolute',
                        top: -99999,
                        left: 0,
                        width: readerContentWidth,
                        visibility: 'hidden',
                        pointerEvents: 'none',
                        zIndex: -1,
                        boxSizing: 'border-box',
                        whiteSpace: 'normal',
                    }} />
                )}
                {mode === 'reading' && lineFocus && !readingLoading && focusLines.length > 0 && (() => {
                    const ln = focusRange(focusLines, focusIdx, focusUnit)!;
                    // 双页时框只框当前这一页那一栏，另一页同一高度也盖上浅色
                    const midX = spread ? (contentRef.current?.clientWidth ?? readerSize.width) / 2 : null;
                    const onLeft = midX != null && (ln.left + ln.right) / 2 < midX;
                    const colLines = midX == null ? focusLines : focusLines.filter(l => ((l.left + l.right) / 2 < midX) === onLeft);
                    const colL = Math.min(...colLines.map(l => l.left)), colR = Math.max(...colLines.map(l => l.right));
                    const padY = Math.max(5, Math.round(readerFontSize * 0.4));
                    const top = ln.top - padY, bottom = ln.bottom + padY;
                    const veil = focusVeil(readerNightMode);
                    const ease = '180ms cubic-bezier(0.23,1,0.32,1)';
                    return (
                        <>
                            <div aria-hidden style={{
                                ...focusGlassStyle(readerNightMode), position: 'absolute', zIndex: 0,
                                left: colL - 10, width: colR - colL + 20, top, height: bottom - top,
                                transition: `top ${ease}, height ${ease}`,
                            }} />
                            <div aria-hidden style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2 }}>
                                <div style={{ position: 'absolute', left: 0, right: 0, top: 0, height: Math.max(0, top), background: veil, transition: `height ${ease}` }} />
                                <div style={{ position: 'absolute', left: 0, right: 0, top: bottom, bottom: 0, background: veil, transition: `top ${ease}` }} />
                                {midX != null && <div style={{ position: 'absolute', ...(onLeft ? { left: midX, right: 0 } : { left: 0, width: midX }), top, height: bottom - top, background: veil, transition: `top ${ease}, height ${ease}` }} />}
                            </div>
                            <div aria-hidden style={{
                                ...focusEdgeStyle(readerNightMode), position: 'absolute', zIndex: 3,
                                left: colL - 10, width: colR - colL + 20, top, height: bottom - top,
                                transition: `top ${ease}, height ${ease}`,
                            }} />
                        </>
                    );
                })()}
            </div>

            {/* Floating annotation bar — appears when text is selected */}
            {floatingBar && mode === 'reading' && commentingIdx === null && (
                <div className="cr-pop-sm-c" style={{
                    position: 'absolute', bottom: 20, left: '50%', transform: 'translateX(-50%)',
                    background: readerNightMode ? '#ebe8e1' : INK, borderRadius: 20, padding: '10px 24px',
                    boxShadow: readerNightMode ? '0 4px 20px rgba(0,0,0,0.3)' : '0 4px 20px rgba(0,0,0,0.15)', zIndex: 25, cursor: 'pointer',
                }}
                    onPointerDown={(e) => { e.preventDefault(); startAnnotation(); }}>
                    <span style={{ color: readerNightMode ? '#222' : '#fff', fontSize: 13, fontWeight: 600 }}>添加批注</span>
                </div>
            )}

            {mode === 'reading' && commentingIdx !== null && !replyingTo && (
                <div onClick={(e) => e.stopPropagation()} className="cr-rise-in" style={{
                    position: 'absolute', left: 16, right: 16, bottom: 20, zIndex: 32,
                    background: readerNightMode ? 'rgba(38,38,38,.62)' : 'rgba(252,252,252,.45)',
                    backdropFilter: 'blur(40px) saturate(1.8)', WebkitBackdropFilter: 'blur(40px) saturate(1.8)' as any,
                    borderRadius: 24, padding: 16,
                    border: readerNightMode ? '1px solid rgba(255,255,255,.07)' : '1px solid rgba(60,55,45,.08)',
                    boxShadow: readerNightMode ? '0 -4px 32px rgba(0,0,0,0.25)' : '0 -4px 32px rgba(0,0,0,0.08)',
                }}>
                    {selectedText && (
                        <div style={{ fontSize: 12, color: readerNightMode ? '#999' : '#777', fontStyle: 'italic', marginBottom: 10, padding: '8px 10px', background: readerNightMode ? 'rgba(255,255,255,.06)' : 'rgba(60,55,45,.05)', borderRadius: 12, lineHeight: 1.5, maxHeight: 96, overflow: 'auto' }} className="no-scrollbar">
                            {selectedText.length > 160 ? selectedText.slice(0, 160) + '...' : selectedText}
                        </div>
                    )}
                    <textarea value={commentText} onChange={e => setCommentText(e.target.value)} placeholder="写下你的想法..."
                        style={{ width: '100%', minHeight: 72, border: 'none', background: 'transparent', fontSize: 14, color: readerNightMode ? '#ddd' : '#444', resize: 'none', outline: 'none', lineHeight: 1.6 }} autoFocus />
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
                        <button onClick={() => { setCommentingIdx(null); setCommentText(''); setSelectedText(''); setSelRange(null); }}
                            style={{ background: 'none', border: `1px solid ${readerNightMode ? 'rgba(255,255,255,.1)' : 'rgba(60,55,45,.1)'}`, borderRadius: 12, padding: '7px 16px', fontSize: 12, color: readerNightMode ? '#888' : '#999', cursor: 'pointer' }}>取消</button>
                        <button onClick={handleAddComment}
                            style={{ background: readerNightMode ? '#ebe8e1' : INK, border: 'none', borderRadius: 12, padding: '7px 18px', fontSize: 12, color: readerNightMode ? '#222' : 'white', cursor: 'pointer', fontWeight: 600, opacity: commentText.trim() ? 1 : 0.5 }}>保存</button>
                    </div>
                </div>
            )}

            {/* Note popup — shows all overlapping annotations */}
            {activeComments.length > 0 && (
                <div onClick={(e) => e.stopPropagation()} style={{
                    position: 'absolute', bottom: 20, left: 16, right: 16,
                    background: readerNightMode ? 'rgba(38,38,38,.62)' : 'rgba(252,252,252,.45)',
                    backdropFilter: 'blur(40px) saturate(1.8)', WebkitBackdropFilter: 'blur(40px) saturate(1.8)' as any,
                    borderRadius: 24, padding: '16px 20px',
                    border: readerNightMode ? '1px solid rgba(255,255,255,.07)' : '1px solid rgba(60,55,45,.08)',
                    boxShadow: readerNightMode ? '0 -4px 32px rgba(0,0,0,0.25)' : '0 -4px 32px rgba(0,0,0,0.08)',
                    zIndex: 20, maxHeight: '50vh', overflow: 'auto',
                }} className="no-scrollbar cr-rise-in">
                    <button onClick={() => setActiveComments([])} style={{ position: 'absolute', top: 10, right: 14, background: 'none', border: 'none', fontSize: 18, color: readerNightMode ? '#666' : '#ccc', cursor: 'pointer', lineHeight: 1, zIndex: 1 }}>×</button>
                    {(() => {
                        const topLevel = activeComments.filter(ac => !ac.reply_to);
                        const replies = activeComments.filter(ac => ac.reply_to);
                        const inkColor = readerNightMode ? '#ccc' : INK;
                        const subColor = readerNightMode ? '#888' : '#999';
                        const dividerColor = readerNightMode ? 'rgba(255,255,255,.08)' : 'rgba(60,55,45,.08)';
                        const renderComment = (ac: Comment, indent: boolean) => {
                            const isShen = ac.from_who.toLowerCase() === 'ai' || ac.from_who.toLowerCase() === aiName.toLowerCase();
                            return (
                                <div key={ac.id} style={{ marginLeft: indent ? 28 : 0, marginBottom: 12, paddingBottom: 12, borderBottom: indent ? 'none' : `1px solid ${dividerColor}` }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                                        <span style={{ fontSize: 12, fontWeight: 600, color: inkColor }}>{displayName(ac.from_who)}</span>
                                        <span style={{ fontSize: 10, color: subColor }}>{ac.created_at?.slice(0, 16).replace('T', ' ')}</span>
                                    </div>
                                    {ac.selected_text && (
                                        <div style={{ fontSize: 12, color: subColor, fontStyle: 'italic', padding: '8px 12px', marginBottom: 10, background: readerNightMode ? 'rgba(255,255,255,.06)' : 'rgba(60,55,45,.05)', borderRadius: 12, lineHeight: 1.5 }}>
                                            {ac.selected_text}
                                        </div>
                                    )}
                                    <div style={{ fontSize: 14, color: readerNightMode ? '#ddd' : 'hsl(40,8%,22%)', lineHeight: 1.7, marginBottom: 8 }}>{ac.content}</div>
                                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                                        <button onClick={() => { replyPageRef.current = page; setReplyingTo(ac); setCommentingIdx(ac.paragraph_idx); setCommentText(''); }} style={{ background: 'none', border: `1px solid ${dividerColor}`, borderRadius: 10, padding: '4px 14px', fontSize: 11, color: inkColor, cursor: 'pointer' }}>回复</button>
                                        {!isShen && (
                                            <button onClick={() => handleDeleteComment(ac)} style={{ background: 'none', border: `1px solid ${readerNightMode ? 'rgba(255,100,100,.2)' : 'rgba(200,100,100,.2)'}`, borderRadius: 10, padding: '4px 14px', fontSize: 11, color: readerNightMode ? '#c88' : '#d88', cursor: 'pointer' }}>删除</button>
                                        )}
                                    </div>
                                </div>
                            );
                        };
                        const renderThread = (parent: Comment, depth: number) => (
                            <React.Fragment key={parent.id}>
                                {renderComment(parent, depth > 0)}
                                {replies.filter(r => r.reply_to === parent.id).map(r => renderThread(r, depth + 1))}
                            </React.Fragment>
                        );
                        return topLevel.map(ac => renderThread(ac, 0));
                    })()}
                    {replyingTo && (
                        <div style={{ marginTop: 8, padding: '10px 12px', background: readerNightMode ? 'rgba(255,255,255,.06)' : 'rgba(60,55,45,.04)', borderRadius: 14, border: `1px solid ${readerNightMode ? 'rgba(255,255,255,.08)' : 'rgba(60,55,45,.08)'}` }}>
                            <div style={{ fontSize: 11, color: readerNightMode ? '#888' : '#999', marginBottom: 6 }}>回复 {displayName(replyingTo.from_who)}：{replyingTo.content.slice(0, 30)}{replyingTo.content.length > 30 ? '…' : ''}</div>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <input value={commentText} onChange={e => setCommentText(e.target.value)} placeholder="写回复…" style={{ flex: 1, border: `1px solid ${readerNightMode ? 'rgba(255,255,255,.1)' : 'rgba(60,55,45,.1)'}`, borderRadius: 10, padding: '6px 12px', fontSize: 13, outline: 'none', background: readerNightMode ? 'rgba(255,255,255,.08)' : 'rgba(255,255,255,.6)', color: readerNightMode ? '#ddd' : '#333' }} onKeyDown={e => e.key === 'Enter' && handleAddComment()} />
                                <button onClick={handleAddComment} style={{ background: readerNightMode ? '#ebe8e1' : INK, color: readerNightMode ? '#222' : '#fff', border: 'none', borderRadius: 10, padding: '6px 14px', fontSize: 12, cursor: 'pointer' }}>发送</button>
                                <button onClick={() => setReplyingTo(null)} style={{ background: 'none', border: `1px solid ${readerNightMode ? 'rgba(255,255,255,.1)' : 'rgba(60,55,45,.1)'}`, borderRadius: 10, padding: '6px 10px', fontSize: 12, color: readerNightMode ? '#888' : '#999', cursor: 'pointer' }}>×</button>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* 跳回图标：小圆形箭头+页码 */}
            {mode === 'reading' && returnPoint && (
                <button onClick={(e) => { e.stopPropagation(); returnToReadingPosition(); }} className="cr-item-in" style={{
                    position: 'absolute', top: 'calc(8px + env(safe-area-inset-top))', left: 12, zIndex: 28,
                    background: 'none', border: 'none', cursor: 'pointer', padding: 6,
                    display: 'flex', alignItems: 'center', gap: 8,
                }}>
                    <span style={{
                        width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                        background: readerNightMode ? 'rgba(255,255,255,0.85)' : 'rgba(40,36,28,0.72)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        color: readerNightMode ? '#222' : '#fff',
                        boxShadow: readerNightMode ? '0 2px 8px rgba(0,0,0,0.3)' : '0 2px 8px rgba(40,35,25,0.22)',
                    }}>
                        <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M4.8 2.6L2.4 5l2.4 2.4M2.4 5h4.4a2.8 2.8 0 012.8 2.8v1.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                    </span>
                    <span style={{ fontSize: 15, fontWeight: 600, color: readerNightMode ? '#aaa' : INK, lineHeight: 1 }}>{returnPoint.page}</span>
                </button>
            )}

            {/* 新批注小胶囊：毛玻璃，固定右下角 */}
            {mode === 'reading' && newReplies.length > 0 && !showReplies && (
                <div onClick={(e) => { e.stopPropagation(); setShowReplies(true); }} style={{
                    position: 'absolute', bottom: 26, right: 12, zIndex: 17,
                    background: readerNightMode ? 'rgba(38,38,38,0.65)' : 'linear-gradient(rgba(255,255,255,0.15), rgba(255,255,255,0) 40%), rgba(252,252,252,0.55)',
                    backdropFilter: 'blur(22px) saturate(1.8)', WebkitBackdropFilter: 'blur(22px) saturate(1.8)' as any,
                    border: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.07)' : 'rgba(60,55,45,0.08)'}`,
                    borderRadius: 999, padding: '8px 14px',
                    boxShadow: readerNightMode ? '0 4px 16px rgba(0,0,0,0.4)' : '0 4px 16px rgba(40,35,25,0.15)', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 6,
                    animation: 'crRiseIn 190ms cubic-bezier(0.23, 1, 0.32, 1) both, pulse 2s ease-in-out 190ms infinite',
                }}>
                    <span style={{ color: readerNightMode ? '#ddd' : INK, fontSize: 12, fontWeight: 600 }}>
                        {(() => { const names = [...new Set(newReplies.map(r => r.from_who).filter(Boolean).map(displayName))]; const who = names.length === 0 ? '新' : names.length === 1 ? names[0] : `${names[0]} 等`; return `${who} · ${newReplies.length} 条新批注`; })()}
                    </span>
                </div>
            )}

            {/* 批注回复弹窗：毛玻璃壳，点遮罩收起 */}
            {showReplies && newReplies.length > 0 && (
                <>
                    <div style={{ position: 'absolute', inset: 0, zIndex: 29 }} onClick={(e) => { e.stopPropagation(); setShowReplies(false); }} />
                    <div onClick={(e) => e.stopPropagation()} style={{
                        position: 'absolute', bottom: 20, right: 16, left: 16, zIndex: 30, borderRadius: 24,
                        background: readerNightMode ? 'rgba(38,38,38,0.75)' : 'linear-gradient(rgba(255,255,255,0.15), rgba(255,255,255,0) 40%), rgba(252,252,252,0.72)',
                        backdropFilter: 'blur(40px) saturate(1.8)', WebkitBackdropFilter: 'blur(40px) saturate(1.8)' as any,
                        border: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.07)' : 'rgba(60,55,45,0.08)'}`,
                        padding: '16px 18px',
                        boxShadow: readerNightMode ? '0 -4px 32px rgba(0,0,0,0.5)' : '0 -4px 32px rgba(40,35,25,0.14), inset 0 1px 0 rgba(255,255,255,0.5)', maxHeight: '55vh', overflow: 'auto',
                    }} className="no-scrollbar cr-rise-in">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                            <span style={{ fontSize: 14, fontWeight: 700, color: readerNightMode ? '#ddd' : INK }}>最新批注回复</span>
                            <button onClick={dismissReplies} style={{ background: readerNightMode ? 'rgba(255,255,255,0.10)' : 'rgba(60,55,45,0.08)', border: 'none', borderRadius: 999, padding: '5px 14px', fontSize: 11, color: readerNightMode ? '#999' : INK2, cursor: 'pointer' }}>已读</button>
                        </div>
                        {newReplies.map(r => (
                            <div key={r.id} onClick={() => openReplyNotice(r)} style={{ marginBottom: 14, paddingBottom: 12, borderBottom: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.07)' : 'rgba(60,55,45,0.08)'}`, cursor: 'pointer' }}>
                                {r.parent_content && (
                                    <div style={{ fontSize: 11, color: readerNightMode ? '#888' : INK2, marginBottom: 6, padding: '4px 10px', background: readerNightMode ? 'rgba(255,255,255,0.05)' : 'rgba(60,55,45,0.05)', borderRadius: 8, borderLeft: `2px solid ${readerNightMode ? 'rgba(255,255,255,0.18)' : 'rgba(60,55,45,0.20)'}` }}>
                                        {r.parent_from}: {r.parent_content.length > 40 ? r.parent_content.slice(0, 40) + '...' : r.parent_content}
                                    </div>
                                )}
                                <div style={{ fontSize: 12, fontWeight: 600, color: readerNightMode ? '#ddd' : INK, marginBottom: 3 }}>{displayName(r.from_who || 'AI')} · 批注</div>
                                <div style={{ fontSize: 13, color: readerNightMode ? '#ccc' : 'hsl(40,6%,30%)', lineHeight: 1.6 }}>{r.content}</div>
                                <div style={{ fontSize: 10, color: readerNightMode ? '#777' : '#aaa', marginTop: 4 }}>{(() => { const pg = findPageForParaIdx(r.paragraph_idx, totalPages, r.sel_start_idx ?? 0); return pg >= 0 ? `p${pg + 1}` : ''; })()} · 点开定位到原文</div>
                            </div>
                        ))}
                    </div>
                </>
            )}

            {/* Bottom toolbar + ≡ floating menu + settings sheet */}
            {mode === 'reading' && (
                <>
                    {/* 页码——常显底部居中 */}
                    <div style={{
                        position: 'absolute', bottom: 12, left: 0, right: 0,
                        textAlign: 'center', fontSize: 11, color: '#bbb', zIndex: 5,
                        pointerEvents: 'none',
                    }}>
                        {spread && page < totalPages ? `${page}-${page + 1}` : page} / {totalPages} 页
                    </div>

                    {/* ≡ floating button */}
                    <button onClick={(e) => { e.stopPropagation(); openReaderMenu(); }} style={{
                        position: 'absolute', right: 16, bottom: 30, width: 44, height: 44, borderRadius: '50%', zIndex: 18,
                        background: readerNightMode ? 'rgba(40,40,40,0.55)' : 'rgba(255,255,255,0.5)',
                        backdropFilter: 'blur(22px) saturate(1.8)', WebkitBackdropFilter: 'blur(22px) saturate(1.8)' as any,
                        border: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.10)'}`,
                        boxShadow: readerNightMode ? '0 2px 10px rgba(0,0,0,0.4)' : '0 2px 10px rgba(60,55,45,0.10), inset 0 1px 0 rgba(255,255,255,0.55)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                        color: readerNightMode ? '#ddd' : INK,
                        visibility: readerMenuPhase === 'closed' ? 'visible' : 'hidden',
                        opacity: fabVisible ? 1 : 0, pointerEvents: fabVisible ? 'auto' : 'none',
                        transition: 'opacity 0.2s ease',
                    }}>
                        <svg width="18" height="18" viewBox="0 0 18 18" fill="none"><path d="M3 5h12M3 9h12M3 13h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>
                    </button>

                    {/* Floating menu: 目录 / 阅读设置 / 辅助阅读 / 阅读钟 + 所有批注 / 导出 / 统计 */}
                    {readerMenuPhase !== 'closed' && (
                        <>
                            <div style={{ position: 'absolute', inset: 0, zIndex: 19 }} onClick={(e) => { e.stopPropagation(); closeReaderMenu(); }} />
                            <div onClick={(e) => e.stopPropagation()}
                                className={'hy-reader ' + (readerMenuPhase === 'anim' ? 'cr-rmenu-anim' : readerMenuPhase === 'closing' ? 'cr-rmenu-closing' : '')}
                                style={{
                                    position: 'absolute', right: 8, bottom: 44, width: 196, zIndex: 20, borderRadius: 24,
                                    background: readerNightMode ? 'rgba(38,38,38,0.6)' : 'linear-gradient(rgba(255,255,255,0.28), rgba(255,255,255,0) 40%), rgba(252,252,252,0.36)',
                                    backdropFilter: 'var(--hyalite, blur(56px) saturate(1.8))', WebkitBackdropFilter: 'var(--hyalite, blur(56px) saturate(1.8))' as any,
                                    boxShadow: 'var(--hyalite-edge, 0 0 0 0 transparent), ' + (readerNightMode ? '0 16px 50px rgba(0,0,0,0.5)' : '0 16px 50px rgba(60,55,45,0.14), inset 0 1px 0 rgba(255,255,255,0.5)'),
                                    border: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.07)' : 'rgba(60,55,45,0.08)'}`,
                                    padding: 6, transformOrigin: 'calc(100% - 30px) calc(100% + 8px)',
                                }}>
                                {(() => {
                                    const mInk = readerNightMode ? '#ddd' : INK;
                                    const mInk2 = readerNightMode ? '#888' : INK2;
                                    const mIcon = readerNightMode ? 'rgba(255,255,255,0.7)' : 'rgba(40,36,28,0.72)';
                                    const rowSt: React.CSSProperties = { display: 'flex', alignItems: 'center', padding: '9px 12px', borderRadius: 16, cursor: 'pointer' };
                                    const labelSt: React.CSSProperties = { fontSize: 14, color: mInk, flex: 1, letterSpacing: '-0.01em' };
                                    const pct = totalPages > 1 ? Math.max(1, Math.round((page / totalPages) * 100)) : 1;
                                    return (
                                        <>
                                            <div style={rowSt} onClick={() => { closeReaderMenu(); setTocPhase('open'); setShowToc(true); }}>
                                                <span style={labelSt}>目录<span style={{ fontSize: 11, color: mInk2, marginLeft: 6 }}>· {pct}%</span></span>
                                                <svg width="17" height="17" viewBox="0 0 17 17" fill="none" style={{ color: mIcon, flexShrink: 0 }}><path d="M3 4.5h11M3 8.5h11M3 12.5h7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                                            </div>
                                            <div style={rowSt} onClick={() => { closeReaderMenu(); setSheetPhase('open'); }}>
                                                <span style={labelSt}>阅读设置</span>
                                                <svg width="17" height="17" viewBox="0 0 17 17" fill="none" style={{ color: mIcon, flexShrink: 0 }}><path d="M3 12.5V4.8c0-.4.4-.8.8-.8H8v8.5H3.8c-.4 0-.8-.4-.8-.8zM14 12.5V4.8c0-.4-.4-.8-.8-.8H9v8.5h4.2c.4 0 .8-.4.8-.8z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>
                                            </div>
                                            <div style={rowSt} onClick={() => { closeReaderMenu(); setAssistPhase('open'); }}>
                                                <span style={labelSt}>辅助阅读</span>
                                                <svg width="17" height="17" viewBox="0 0 17 17" fill="none" style={{ color: mIcon, flexShrink: 0 }}><rect x="2.5" y="6.6" width="12" height="3.8" rx="1.9" stroke="currentColor" strokeWidth="1.3"/><path d="M4 3.6h9M4 13.4h6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
                                            </div>
                                            <div style={rowSt} onClick={() => { const v = !showSessionClock; setShowSessionClock(v); localStorage.setItem('coread-show-session-clock', String(v)); }}>
                                                <span style={labelSt}>阅读钟</span>
                                                <ReaderSwitch on={showSessionClock} night={readerNightMode} />
                                            </div>
                                            <div style={{ height: 0.5, background: readerNightMode ? 'rgba(255,255,255,0.12)' : 'hsla(40,10%,40%,0.16)', margin: '4px 12px' }} />
                                            <div style={{ display: 'flex', justifyContent: 'space-around', padding: '6px 8px' }}>
                                                <div title="所有批注" onClick={() => { closeReaderMenu(); setAllNotesPhase('open'); }} style={{ width: 46, height: 36, borderRadius: 14, background: readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.07)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: mInk, cursor: 'pointer' }}>
                                                    <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><rect x="2.6" y="3.2" width="11.8" height="9.4" rx="2.8" stroke="currentColor" strokeWidth="1.3"/><path d="M5.6 6.6h5.8M5.6 9.3h3.8" stroke="currentColor" strokeWidth="1.15" strokeLinecap="round"/></svg>
                                                </div>
                                                <div title="导出文本" onClick={() => { closeReaderMenu(); handleExport(); }} style={{ width: 46, height: 36, borderRadius: 14, background: readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.07)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: mInk, cursor: 'pointer' }}>
                                                    <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><path d="M8.5 9.8V2.5M8.5 2.5L6.2 4.8M8.5 2.5l2.3 2.3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/><path d="M3.3 8.6v2.9a2.2 2.2 0 002.2 2.2h6a2.2 2.2 0 002.2-2.2V8.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
                                                </div>
                                                <div title="本书统计" onClick={() => { closeReaderMenu(); openStatsDrawer('book'); }} style={{ width: 46, height: 36, borderRadius: 14, background: readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.07)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: mInk, cursor: 'pointer' }}>
                                                    <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><circle cx="8.5" cy="8.5" r="5.7" stroke="currentColor" strokeWidth="1.3"/><path d="M8.5 5.3v3.2l2.3 1.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>
                                                </div>
                                            </div>
                                        </>
                                    );
                                })()}
                            </div>
                        </>
                    )}

                    {/* 阅读设置抽屉：玻璃壳，亮度/字号/日夜间 */}
                    {sheetPhase !== 'closed' && (
                        <>
                            <div className={sheetPhase === 'closing' ? 'cr-scrim-out' : 'cr-scrim-in'}
                                onClick={(e) => { e.stopPropagation(); setSheetPhase('closing'); setTimeout(() => setSheetPhase(p => p === 'closing' ? 'closed' : p), 240); }}
                                style={readerScrimStyle(readerNightMode)} />
                            <div onClick={(e) => e.stopPropagation()}
                                className={'hy-shelf ' + (sheetPhase === 'closing' ? 'cr-sheet-out' : 'cr-sheet-in')}
                                style={readerSheetStyle(readerNightMode)}>
                                {(() => {
                                    const sInk = readerNightMode ? '#ddd' : INK;
                                    const sInk2 = readerNightMode ? '#888' : INK2;
                                    const briPct = (readerBrightness - 30) / 70;
                                    return (
                                        <>
                                            <ReaderSheetHeader title="阅读设置" night={readerNightMode} onClose={() => { setSheetPhase('closing'); setTimeout(() => setSheetPhase(p => p === 'closing' ? 'closed' : p), 240); }} />
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14, padding: '0 4px' }}>
                                                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ color: sInk2, flexShrink: 0 }}><circle cx="7" cy="7" r="2.6" stroke="currentColor" strokeWidth="1.2"/><path d="M7 1.5v1.3M7 11.2v1.3M1.5 7h1.3M11.2 7h1.3M3.2 3.2l.9.9M9.9 9.9l.9.9M10.8 3.2l-.9.9M4.1 9.9l-.9.9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>
                                                <div style={{ position: 'relative', flex: 1, height: 22, display: 'flex', alignItems: 'center' }}>
                                                    <div style={{ position: 'absolute', left: 0, right: 0, height: 3, borderRadius: 999, background: readerNightMode ? 'rgba(255,255,255,0.18)' : 'rgba(60,55,45,0.16)' }}>
                                                        <div style={{ width: `${briPct * 100}%`, height: '100%', borderRadius: 999, background: readerNightMode ? '#ccc' : INK }} />
                                                    </div>
                                                    <div style={{ position: 'absolute', left: `calc((100% - 20px) * ${briPct})`, width: 20, height: 20, borderRadius: '50%', background: readerNightMode ? '#ddd' : '#fff', boxShadow: '0 1px 5px rgba(40,35,25,0.25)', pointerEvents: 'none' }} />
                                                    <input type="range" min={30} max={100} step={1} value={readerBrightness}
                                                        onChange={e => { const v = parseInt(e.target.value, 10); setReaderBrightness(v); localStorage.setItem('coread-brightness', String(v)); }}
                                                        style={{ position: 'absolute', inset: 0, width: '100%', opacity: 0, cursor: 'pointer', margin: 0 }} />
                                                </div>
                                                <svg width="17" height="17" viewBox="0 0 17 17" fill="none" style={{ color: sInk2, flexShrink: 0 }}><circle cx="8.5" cy="8.5" r="3.2" stroke="currentColor" strokeWidth="1.3"/><path d="M8.5 1.5v1.7M8.5 13.8v1.7M1.5 8.5h1.7M13.8 8.5h1.7M3.5 3.5l1.2 1.2M12.3 12.3l1.2 1.2M13.5 3.5l-1.2 1.2M4.7 12.3l-1.2 1.2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14, borderRadius: 999, overflow: 'hidden', border: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.12)' : 'rgba(60,55,45,0.12)'}`, background: readerNightMode ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.45)' }}>
                                                <button onClick={() => { const v = Math.max(12, readerFontSize - 1); setReaderFontSize(v); localStorage.setItem('coread-font-size', String(v)); }}
                                                    style={{ flex: 1, padding: '9px 0', background: 'none', border: 'none', cursor: 'pointer', fontSize: 15, color: sInk }}>
                                                    A<span style={{ fontSize: 10, verticalAlign: 'super' }}>−</span>
                                                </button>
                                                <span style={{ padding: '9px 16px', fontSize: 13, fontWeight: 600, color: sInk, textAlign: 'center', minWidth: 40, borderLeft: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.12)' : 'rgba(60,55,45,0.12)'}`, borderRight: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.12)' : 'rgba(60,55,45,0.12)'}` }}>
                                                    {readerFontSize}
                                                </span>
                                                <button onClick={() => { const v = Math.min(22, readerFontSize + 1); setReaderFontSize(v); localStorage.setItem('coread-font-size', String(v)); }}
                                                    style={{ flex: 1, padding: '9px 0', background: 'none', border: 'none', cursor: 'pointer', fontSize: 15, color: sInk }}>
                                                    A<span style={{ fontSize: 10, verticalAlign: 'super' }}>+</span>
                                                </button>
                                            </div>
                                            <div style={{ display: 'flex', gap: 8 }}>
                                                <button onClick={() => { setReaderNightMode(false); localStorage.setItem('coread-night-mode', 'false'); }}
                                                    style={{ flex: 1, padding: '9px 18px 9px 0', borderRadius: 999, border: `1.5px solid ${!readerNightMode ? INK : (readerNightMode ? 'rgba(255,255,255,0.12)' : 'rgba(60,55,45,0.10)')}`, background: !readerNightMode ? 'rgba(60,55,45,0.07)' : 'transparent', cursor: 'pointer', fontSize: 13, color: !readerNightMode ? INK : (readerNightMode ? '#888' : INK2), fontWeight: !readerNightMode ? 600 : 500, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5 }}>
                                                    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ display: 'block', flexShrink: 0 }}><circle cx="6.5" cy="6.5" r="2.4" stroke="currentColor" strokeWidth="1.2"/><path d="M6.5 1.4v1M6.5 10.6v1M1.4 6.5h1M10.6 6.5h1M2.9 2.9l.7.7M9.4 9.4l.7.7M10.1 2.9l-.7.7M3.6 9.4l-.7.7" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>日间
                                                </button>
                                                <button onClick={() => { setReaderNightMode(true); localStorage.setItem('coread-night-mode', 'true'); }}
                                                    style={{ flex: 1, padding: '9px 18px 9px 0', borderRadius: 999, border: `1.5px solid ${readerNightMode ? '#aaa' : 'rgba(60,55,45,0.10)'}`, background: readerNightMode ? 'rgba(255,255,255,0.10)' : 'transparent', cursor: 'pointer', fontSize: 13, color: readerNightMode ? '#eee' : INK2, fontWeight: readerNightMode ? 600 : 500, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5 }}>
                                                    <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ display: 'block', flexShrink: 0 }}><path d="M11.4 6.9A4.9 4.9 0 1 1 6.1 1.6 3.8 3.8 0 0 0 11.4 6.9z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/></svg>夜间
                                                </button>
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 14 }}>
                                                <span style={{ fontSize: 13, fontWeight: 600, color: sInk, flexShrink: 0 }}>翻页</span>
                                                <ReaderSegPill night={readerNightMode} value={pageTurnMode} options={[['滑动', 'swipe'], ['点按', 'tap']]}
                                                    onChange={(val) => { setPageTurnMode(val); localStorage.setItem('coread-page-turn-mode', val); }} />
                                            </div>
                                            {/* 版式：横屏够宽时摊成左右两页 */}
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 14 }}>
                                                <span style={{ fontSize: 13, fontWeight: 600, color: sInk, flexShrink: 0 }}>版式</span>
                                                <ReaderSegPill night={readerNightMode} value={pageLayout} options={[['自动', 'auto'], ['单页', 'single'], ['双页', 'double']]}
                                                    onChange={(val) => { setPageLayout(val); lsSet('coread-page-layout', val); }} />
                                            </div>
                                        </>
                                    );
                                })()}
                            </div>
                        </>
                    )}

                    {/* 辅助阅读抽屉：壳与阅读设置同款；示范文字走正文同一套渲染 */}
                    {assistPhase !== 'closed' && (
                        <>
                            <div className={assistPhase === 'closing' ? 'cr-scrim-out' : 'cr-scrim-in'}
                                onClick={(e) => { e.stopPropagation(); closeAssistSheet(); }}
                                style={readerScrimStyle(readerNightMode)} />
                            <div onClick={(e) => e.stopPropagation()}
                                className={'hy-shelf ' + (assistPhase === 'closing' ? 'cr-sheet-out' : 'cr-sheet-in')}
                                style={{ ...readerSheetStyle(readerNightMode), height: 'min(50vh, calc(100% - 16px))', display: 'flex', flexDirection: 'column', boxSizing: 'border-box' }}>
                                <ReaderSheetHeader title="辅助阅读" night={readerNightMode} onClose={closeAssistSheet} style={{ flexShrink: 0 }} />
                                {(() => {
                                    const sInk = readerNightMode ? '#ddd' : INK;
                                    const sInk2 = readerNightMode ? '#888' : INK2;
                                    const rowSt: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 14, marginTop: 14, cursor: 'pointer' };
                                    const labelSt: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: sInk, flex: 1, minWidth: 0 };
                                    const hintSt: React.CSSProperties = { fontSize: 11, fontWeight: 400, color: sInk2, marginLeft: 6 };
                                    const demoFs = readerFontSize;
                                    const demoSp = assistSpacing(1.85, 0.3, demoFs, assistSpacingLv);
                                    const demoLn = focusRange(assistDemoLines, 0, focusUnit);
                                    const demoCol = assistDemoLines.length ? { l: Math.min(...assistDemoLines.map(l => l.left)), r: Math.max(...assistDemoLines.map(l => l.right)) } : null;
                                    const padY = Math.max(5, Math.round(demoFs * 0.4));
                                    const toggle = (label: string, hint: string, on: boolean, set: (v: boolean) => void, key: string, needsSeg: boolean) => {
                                        const off = needsSeg && !segmenter;
                                        return (
                                            <div key={label} role="switch" aria-checked={on && !off} style={{ ...rowSt, opacity: off ? 0.45 : 1, cursor: off ? 'default' : 'pointer' }}
                                                onClick={() => { if (off) return; const v = !on; set(v); lsSet(key, String(v)); }}>
                                                <span style={labelSt}>{label}<span style={hintSt}>{off ? '此浏览器不支持' : hint}</span></span>
                                                <ReaderSwitch on={on && !off} night={readerNightMode} />
                                            </div>
                                        );
                                    };
                                    return (
                                        <div className="no-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', margin: '0 -4px', padding: '0 4px 2px', scrollbarWidth: 'none' }}>
                                            <div ref={assistDemoRef} style={{
                                                position: 'relative', borderRadius: 16, padding: '12px 16px', overflow: 'hidden',
                                                background: readerNightMode ? '#1a1a1a' : '#fffcf3',
                                                border: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.12)' : 'rgba(60,55,45,0.12)'}`,
                                            }}>
                                                {demoLn && demoCol && (
                                                    <div aria-hidden style={{ ...focusGlassStyle(readerNightMode), position: 'absolute', zIndex: 0, left: demoCol.l - 8, width: demoCol.r - demoCol.l + 16, top: demoLn.top - padY, height: demoLn.bottom - demoLn.top + padY * 2 }} />
                                                )}
                                                <div data-demo-text style={{
                                                    position: 'relative', zIndex: 1, fontSize: demoFs, lineHeight: demoSp.lineHeight, letterSpacing: demoSp.letterSpacing,
                                                    color: readerNightMode ? '#ccc' : '#333', textIndent: '1.5em', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word',
                                                } as React.CSSProperties}>
                                                    {assistOn ? renderAssistText(ASSIST_DEMO, 0, ASSIST_DEMO.length, demoFs, -1, []) : ASSIST_DEMO}
                                                </div>
                                                {demoLn && demoCol && (<>
                                                    <div aria-hidden style={{ position: 'absolute', left: 0, right: 0, top: demoLn.bottom + padY, bottom: 0, zIndex: 2, background: focusVeil(readerNightMode), pointerEvents: 'none' }} />
                                                    <div aria-hidden style={{ ...focusEdgeStyle(readerNightMode), position: 'absolute', zIndex: 3, left: demoCol.l - 8, width: demoCol.r - demoCol.l + 16, top: demoLn.top - padY, height: demoLn.bottom - demoLn.top + padY * 2 }} />
                                                </>)}
                                            </div>
                                            {toggle('阅读尺', focusUnit === 'para' ? '逐段聚焦，点按走段' : '逐行聚焦，点按走行', lineFocus, setLineFocus, 'coread-line-focus', false)}
                                            {lineFocus && (
                                                <div style={{ ...rowSt, cursor: 'default' }}>
                                                    <span style={{ ...labelSt, flex: 'none' }}>聚焦</span>
                                                    <ReaderSegPill night={readerNightMode} value={focusUnit} options={[['逐行', 'line'], ['逐段', 'para']]}
                                                        onChange={(v) => {
                                                            // 逐段切回逐行：落到当前这段的第一行，不停在段尾
                                                            if (v === 'line' && focusUnit === 'para') { const r = focusRange(focusLines, focusIdx, 'para') as FocusBlock | null; if (r) setFocusIdx(r.first); }
                                                            setFocusUnit(v as FocusUnit); lsSet('coread-line-focus-unit', String(v));
                                                        }} />
                                                </div>
                                            )}
                                            {toggle('切词', '词与词之间留空', assistSplit, setAssistSplit, 'coread-assist-split', true)}
                                            {toggle('加粗', '实词加粗', assistBold, setAssistBold, 'coread-assist-bold', true)}
                                            {toggle('放大', '实词放大，虚词变浅', assistEnlarge, setAssistEnlarge, 'coread-assist-enlarge', true)}
                                            <div style={{ ...rowSt, cursor: 'default' }}>
                                                <span style={{ ...labelSt, flex: 'none' }}>间距</span>
                                                <ReaderSegPill night={readerNightMode} value={assistSpacingLv} options={[['标准', 0], ['宽', 1], ['更宽', 2]]}
                                                    onChange={(v) => { setAssistSpacingLv(v as SpacingLevel); lsSet('coread-assist-spacing', String(v)); }} />
                                            </div>
                                        </div>
                                    );
                                })()}
                            </div>
                        </>
                    )}
                </>
            )}

            {/* 阅读统计抽屉（statsPhase三相位） */}
            {statsPhase !== 'closed' && (() => {
                const sInk = readerNightMode ? '#ddd' : INK;
                const sInk2 = readerNightMode ? '#888' : INK2;
                const sPaper = readerNightMode ? '#242424' : '#fffcf3';
                const sBorder = readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.10)';
                const closeStats = closeStatsDrawer;
                const wedgeFill = readerNightMode ? '#BAD6EB' : '#7096D1';
                const todayStroke = readerNightMode ? '#D0E3FF' : '#334EAC';
                const centerDot = readerNightMode ? '#BAD6EB' : '#334EAC';
                const HEAT_LV: [string, number][] = readerNightMode
                    ? [['#BAD6EB', 0.3], ['#BAD6EB', 0.55], ['#7096D1', 0.85]]
                    : [['#BAD6EB', 0.75], ['#7096D1', 0.55], ['#334EAC', 0.55]];
                const heatQuiet = readerNightMode ? 'rgba(255,255,255,0.16)' : 'rgba(8,31,92,0.14)';
                const rimStroke = readerNightMode ? 'rgba(255,255,255,0.22)' : 'rgba(88,64,46,0.32)';
                const rimLabel = readerNightMode ? '#888' : 'rgba(88,64,46,0.60)';
                const cutoff = new Date(); cutoff.setHours(0, 0, 0, 0); cutoff.setDate(cutoff.getDate() - 83);
                const bookScoped = statsScope === 'book' && !!activeBook;
                const sessions = ((readingStats?.sessions || []) as any[])
                    .filter((s: any) => s.reading_date >= localDay(cutoff.getTime()) && Number(s.elapsed_ms) >= 60 * 1000)
                    .filter((s: any) => !bookScoped || s.book_id === activeBook!.id)
                    .sort((a: any, b: any) => a.reading_date < b.reading_date ? -1 : a.reading_date > b.reading_date ? 1 : (a.start_min ?? 720) - (b.start_min ?? 720));
                const bookRow = bookScoped ? ((readingStats?.books || []) as any[]).find((b: any) => b.id === activeBook!.id) : null;
                const bookToday = bookScoped ? ((readingStats?.by_day || []) as any[]).filter((d: any) => d.book_id === activeBook!.id && d.reading_date === localDay(Date.now())).reduce((a: number, d: any) => a + Number(d.seconds || 0), 0) : 0;
                const bookDays = bookScoped ? new Set(((readingStats?.by_day || []) as any[]).filter((d: any) => d.book_id === activeBook!.id).map((d: any) => d.reading_date)).size : 0;
                const todayKey = localDay(Date.now());
                const dayLabel = (d?: string) => { if (!d) return '—'; const [, m, dd] = d.split('-').map(Number); if (d === todayKey) return '今天'; if (d === localDay(Date.now() - 86400000)) return '昨天'; return `${m} 月 ${dd} 日`; };
                const firstReadLabel = (d?: string) => { if (!d) return ''; const [, m, dd] = d.split('-').map(Number); return `${m} 月 ${dd} 日起`; };
                const rnd = (i: number, k: number) => { const x = Math.sin(i * 127.1 + k * 311.7) * 43758.5453; return x - Math.floor(x); };
                const CX = 200, CY = 168;
                const pol = (r: number, deg: number): [number, number] => [CX + r * Math.cos(deg * Math.PI / 180), CY + r * Math.sin(deg * Math.PI / 180)];
                const sect = (r0: number, r1: number, a0: number, a1: number) => {
                    const [x0, y0] = pol(r1, a0), [x1, y1] = pol(r1, a1), [x2, y2] = pol(r0, a1), [x3, y3] = pol(r0, a0);
                    const large = a1 - a0 > 180 ? 1 : 0;
                    return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r1} ${r1} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)} L${x2.toFixed(1)} ${y2.toFixed(1)} A${r0} ${r0} 0 ${large} 0 ${x3.toFixed(1)} ${y3.toFixed(1)} Z`;
                };
                const wedges = sessions.map((s: any, i: number) => {
                    const mins = Number(s.elapsed_ms) / 60000;
                    const startMin = Number.isFinite(s.start_min) ? s.start_min : 12 * 60;
                    const a0 = -90 + (startMin / 60) * 15;
                    const sw = Math.max(2.5, Math.min(90, mins * 0.25));
                    const r1 = Math.min(134, 34 + Math.sqrt(mins) * 12);
                    return { a0, sw, r1, key: `${s.reading_date}-${s.session_id || i}`, today: s.reading_date === todayKey, op: 0.12 + rnd(i, 6) * 0.13, fill: wedgeFill };
                });
                const HEAT_WEEKS = 24, HX0 = 34, HY0 = 34, HP = 15;
                const heatSec = new Map(((readingStats?.daily || []) as any[]).map((d: any) => [d.reading_date, Number(d.seconds || 0)]));
                const heatEnd = new Date(); heatEnd.setHours(0, 0, 0, 0);
                const heatFirst = new Date(heatEnd); heatFirst.setDate(heatFirst.getDate() - ((heatEnd.getDay() + 6) % 7) - (HEAT_WEEKS - 1) * 7);
                const heatCells: { w: number; d: number; key: string; sec: number; month: number }[] = [];
                for (let w = 0; w < HEAT_WEEKS; w++) for (let d = 0; d < 7; d++) {
                    const dt = new Date(heatFirst); dt.setDate(heatFirst.getDate() + w * 7 + d);
                    if (dt > heatEnd) continue;
                    const key = localDay(dt.getTime());
                    heatCells.push({ w, d, key, sec: heatSec.get(key) || 0, month: dt.getMonth() });
                }
                const heatMax = Math.max(60, ...heatCells.map(c => c.sec));
                const heatPeak = heatCells.filter(c => c.sec >= 60).reduce<typeof heatCells[number] | null>((a, c) => !a || c.sec > a.sec ? c : a, null);
                const heatMonths = heatCells.filter(c => c.d === 0).filter((c, i, arr) => i === 0 || c.month !== arr[i - 1].month);
                if (heatMonths.length > 1 && heatMonths[1].w - heatMonths[0].w < 3) heatMonths.shift();
                const heatR = (sec: number) => 1.3 + Math.sqrt(sec / heatMax) * 5.2;
                const heatLv = (sec: number) => HEAT_LV[sec > heatMax * 0.66 ? 2 : sec > heatMax * 0.33 ? 1 : 0];
                const sectionTitle: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: sInk, margin: '18px 2px 2px' };
                const sectionSub: React.CSSProperties = { fontSize: 11, color: sInk2, margin: '0 2px 4px' };
                const summaryCard: React.CSSProperties = { flex: 1, borderRadius: 16, background: readerNightMode ? 'rgba(255,255,255,0.07)' : 'rgba(60,55,45,0.06)', padding: '12px 14px' };
                const clockSeconds = readingClock?.seconds || 0;
                return (
                <>
                    <div className={statsPhase === 'closing' ? 'cr-scrim-out' : 'cr-scrim-in'}
                        onClick={closeStats}
                        style={{ position: 'absolute', inset: 0, zIndex: 30, background: readerNightMode ? 'rgba(0,0,0,0.5)' : 'rgba(30,26,20,0.35)' }} />
                    <div onClick={(e) => e.stopPropagation()}
                        className={statsPhase === 'closing' ? 'cr-sheet-out' : 'cr-sheet-in'}
                        style={{
                            position: 'absolute', left: 0, right: 0, bottom: 0, top: 56, zIndex: 31,
                            borderRadius: '24px 24px 0 0', background: sPaper,
                            boxShadow: readerNightMode ? '0 -8px 40px rgba(0,0,0,0.5)' : '0 -8px 40px rgba(40,35,25,0.18)',
                            display: 'flex', flexDirection: 'column', overflow: 'hidden',
                        }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '16px 18px 12px', borderBottom: `1px solid ${sBorder}`, flexShrink: 0 }}>
                            {bookScoped && (
                                <div style={{ width: 34, height: 46, borderRadius: 6, flexShrink: 0, overflow: 'hidden', background: activeBook?.cover_image ? '#f0ebe3' : BOOK_COVERS[(activeBook?.id || 0) % BOOK_COVERS.length], border: `1px solid ${sBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 700, color: sInk2 }}>
                                    {activeBook?.cover_image
                                        ? <img src={`${BASE}/v1/book-images/${activeBook.id}/${activeBook.cover_image}`} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                        : (activeBook?.title || '书').charAt(0)}
                                </div>
                            )}
                            <div onClick={bookScoped ? undefined : () => loadReadingStats(statsReader === 'tongbao' ? 'ai' : 'tongbao')}
                                style={{ flex: 1, minWidth: 0, cursor: bookScoped ? 'default' : 'pointer' }}>
                                <div style={{ fontSize: 15, fontWeight: 700, color: sInk, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}>
                                    {bookScoped ? (activeBook?.title || '本书统计') : `${statsReader === 'ai' ? aiName : humanName}的阅读统计`}
                                    {!bookScoped && <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ color: sInk2, flexShrink: 0 }}><path d="M2 4.5h8.5M8.5 2l2.5 2.5L8.5 7M11 8.5H2.5M4.5 6L2 8.5 4.5 11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>}
                                </div>
                                <div style={{ fontSize: 12, color: sInk2, marginTop: 2 }}>{bookScoped ? '本书统计' : `全部书籍 · 点这里看${statsReader === 'ai' ? humanName : aiName}的`}</div>
                            </div>
                            <button onClick={closeStats} style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, background: readerNightMode ? 'rgba(255,255,255,0.10)' : 'rgba(60,55,45,0.08)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', color: sInk2, cursor: 'pointer' }}>
                                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
                            </button>
                        </div>
                        <div key={statsOpenSeq} style={{ flex: 1, overflow: 'auto', padding: '8px 18px 24px' }} className="no-scrollbar">
                            {readingStatsLoading ? (
                                <div style={{ padding: '40px 0', textAlign: 'center', fontSize: 13, color: sInk2 }}>正在整理阅读足迹…</div>
                            ) : !readingStats ? (
                                <div style={{ padding: '40px 0', textAlign: 'center', fontSize: 13, color: sInk2 }}>暂时读不到统计，稍后再试。</div>
                            ) : (
                                <>
                                    {bookScoped ? (
                                        <>
                                            <div className="cr-item-in" style={{ textAlign: 'center', padding: '14px 0 18px' }}>
                                                <div style={{ fontSize: 34, fontWeight: 700, color: sInk, letterSpacing: '-0.01em' }}>{fmtDuration(Number(bookRow?.total_seconds || 0))}</div>
                                                <div style={{ fontSize: 12, color: sInk2, marginTop: 4 }}>{bookRow ? `本书累计阅读 · ${firstReadLabel(bookRow.first_read_at)}` : '本书还没有阅读记录'}</div>
                                            </div>
                                            {[
                                                { k: '本次阅读', v: fmtClock(clockSeconds) },
                                                { k: '状态', v: Number(bookRow?.total_seconds || 0) > 0 ? '看过' : '没看过' },
                                                { k: '最近阅读', v: bookRow ? dayLabel(bookRow.last_read_at) : '—' },
                                            ].map((r, ri) => (
                                                <div key={r.k} className="cr-item-in" style={{ animationDelay: `${0.08 + ri * 0.06}s`, display: 'flex', alignItems: 'center', padding: '12px 4px', fontSize: 14, color: sInk, borderTop: ri === 0 ? `0.5px solid ${sBorder}` : 'none', borderBottom: `0.5px solid ${sBorder}` }}>
                                                    {r.k}<span style={{ marginLeft: 'auto', color: sInk2, fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>{r.v}</span>
                                                </div>
                                            ))}
                                        </>
                                    ) : (
                                    <>
                                    <div style={{ display: 'flex', gap: 8, margin: '8px 0 4px' }}>
                                        <div style={summaryCard}><div style={{ fontSize: 16, fontWeight: 700, color: sInk }}>{fmtDuration(bookScoped ? bookToday : readingStats.today_seconds)}</div><div style={{ fontSize: 11, color: sInk2, marginTop: 3 }}>今日</div></div>
                                        <div style={summaryCard}><div style={{ fontSize: 16, fontWeight: 700, color: sInk }}>{fmtDuration(bookScoped ? Number(bookRow?.total_seconds || 0) : readingStats.total_seconds)}</div><div style={{ fontSize: 11, color: sInk2, marginTop: 3 }}>累计</div></div>
                                        {bookScoped
                                            ? <div style={summaryCard}><div style={{ fontSize: 16, fontWeight: 700, color: sInk }}>{bookDays} 天</div><div style={{ fontSize: 11, color: sInk2, marginTop: 3 }}>读过天数</div></div>
                                            : <div style={summaryCard}><div style={{ fontSize: 16, fontWeight: 700, color: sInk }}>{readingStats.currentStreak || 0} 天</div><div style={{ fontSize: 11, color: sInk2, marginTop: 3 }}>连续阅读</div></div>}
                                    </div>
                                    <div style={sectionTitle}>每天读了多少</div>
                                    <div style={sectionSub}>一个点是一天 · 点越大越深读得越久 · 小灰点是没读的日子</div>
                                    <svg viewBox="0 0 400 186" style={{ width: '100%', display: 'block' }}>
                                        {['一', '三', '五', '日'].map((t, k) => (
                                            <text key={`hd${k}`} x={HX0 - 12} y={HY0 + k * 2 * HP + 3} fontSize="8" fontWeight="700" fill={rimLabel} textAnchor="end">{t}</text>
                                        ))}
                                        {heatMonths.map(c => (
                                            <g key={`hm${c.key}`} className="cr-pw-in">
                                                <text x={HX0 + c.w * HP - 3} y={HY0 - 17} fontSize="8" fontWeight="700" fill={rimLabel}>{c.month + 1}月</text>
                                                <line x1={HX0 + c.w * HP} y1={HY0 - 12} x2={HX0 + c.w * HP} y2={HY0 - 7} stroke={rimStroke} strokeWidth={1} />
                                            </g>
                                        ))}
                                        {heatCells.map(c => {
                                            const cx = HX0 + c.w * HP, cy = HY0 + c.d * HP;
                                            const delay = { animationDelay: `${0.1 + c.w * 0.035 + c.d * 0.012}s` };
                                            if (c.sec < 60) return <circle key={c.key} cx={cx} cy={cy} r={0.8} fill={heatQuiet} className="cr-heat-pop" style={delay} />;
                                            const [fill, op] = heatLv(c.sec);
                                            return <circle key={c.key} cx={cx} cy={cy} r={heatR(c.sec)} fill={fill} fillOpacity={op} className="cr-heat-pop" style={delay} />;
                                        })}
                                        {(() => {
                                            const t = heatCells[heatCells.length - 1];
                                            if (!t) return null;
                                            const tx = HX0 + t.w * HP, ty = HY0 + t.d * HP;
                                            return <g className="cr-pw-in" style={{ animationDelay: '1s' }}>
                                                <circle cx={tx} cy={ty} r={7.5} fill="none" stroke={todayStroke} strokeOpacity={0.7} strokeWidth={1} />
                                                <text x={tx + 3} y={HY0 + 7 * HP + 22} fontSize="8" fill={rimLabel} textAnchor="end">今天</text>
                                            </g>;
                                        })()}
                                        {heatPeak && (() => {
                                            const px = HX0 + heatPeak.w * HP, py = HY0 + heatPeak.d * HP, pr = heatR(heatPeak.sec);
                                            const left = px > 230, ly = HY0 + 6 * HP + 24, ex = left ? px - 22 : px + 22;
                                            return <g className="cr-pw-in" style={{ animationDelay: '1.1s' }}>
                                                <circle cx={px} cy={py} r={pr + 3.4} fill="none" stroke={todayStroke} strokeOpacity={0.6} strokeWidth={1.1} strokeDasharray="2 2.5" />
                                                <path d={`M${px} ${py + pr + 5} C${px} ${ly - 8} ${(px + ex) / 2} ${ly - 3} ${ex} ${ly - 3}`} fill="none" stroke={readerNightMode ? 'rgba(186,214,235,0.4)' : '#BAD6EB'} strokeWidth={1.1} />
                                                <text x={left ? ex - 4 : ex + 4} y={ly} fontSize="8" fontStyle="italic" fill={rimLabel} textAnchor={left ? 'end' : 'start'}>读得最久的一天 · {fmtDuration(heatPeak.sec)}</text>
                                            </g>;
                                        })()}
                                    </svg>
                                    <div style={sectionTitle}>一般几点读</div>
                                    <div style={sectionSub}>近 12 周 · 一瓣是一次读书 · 点一下图看说明</div>
                                    <svg viewBox="0 0 400 352" onClick={() => setStatsCaption(v => !v)} style={{ width: '100%', display: 'block', marginTop: 4, cursor: 'pointer' }}>
                                        {Array.from({ length: 96 }, (_, h) => {
                                            const a = -90 + h * 3.75;
                                            const [x1, y1] = pol(140, a);
                                            const [x2, y2] = pol(h % 4 === 0 ? 146 : 143, a);
                                            return <line key={`t${h}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke={rimStroke} strokeWidth={h % 4 === 0 ? 1 : 0.5} className="cr-pw-in" style={{ animationDelay: `${h * 0.006}s` }} />;
                                        })}
                                        {[0, 6, 12, 18].map(h => {
                                            const [x, y] = pol(157, -90 + h * 15);
                                            return <text key={`l${h}`} x={x} y={y + 3} fontSize="8" fontWeight="700" fill={rimLabel} textAnchor="middle" className="cr-pw-in">{String(h).padStart(2, '0')}</text>;
                                        })}
                                        {wedges.map((w, i) => (
                                            <path key={w.key} d={sect(16, w.r1, w.a0, w.a0 + w.sw)} fill={w.fill} fillOpacity={w.op}
                                                className="cr-pw-in" style={{ animationDelay: `${0.2 + i * 0.022}s` }} />
                                        ))}
                                        {wedges.filter(w => w.today).map((w, k) => (
                                            <path key={`o${w.key}`} d={sect(16, w.r1, w.a0, w.a0 + w.sw)} fill="none" stroke={todayStroke} strokeWidth={1.1}
                                                className="cr-pw-in" style={{ animationDelay: `${1.2 + k * 0.15}s` }} />
                                        ))}
                                        <circle cx={CX} cy={CY} r={3} fill={centerDot} className="cr-pw-in" style={{ animationDelay: '1.3s' }} />
                                        {(statsCaption || !wedges.length) && (
                                            <text x={CX} y={346} fontSize="7.5" fontWeight="600" fill={rimLabel} textAnchor="middle" letterSpacing="1.2" className="cr-pw-in" style={{ animationDelay: '0s' }}>
                                                {wedges.length ? '角度 = 几点开始 · 长短 = 读了多久 · 叠得越深越常在这个钟点读 · 描边 = 今天' : '近 12 周还没有阅读记录'}
                                            </text>
                                        )}
                                    </svg>
                                    </>
                                    )}
                                </>
                            )}
                        </div>
                    </div>
                </>
                );
            })()}


            {/* ⋯ 收纳菜单：雾面玻璃，从 ⋯ 按钮 morph 出来 */}
            {menuPhase !== 'closed' && (
                <>
                    <div style={{ position: 'absolute', inset: 0, zIndex: 19 }}
                        onClick={() => { if (menuDetail) closeMenuDetail(); else setShowMenu(false); }} />
                    <div ref={menuRef}
                        className={'hy-shelf ' + (menuPhase === 'anim' ? 'cr-pop-anim' : menuPhase === 'closing' ? 'cr-pop-closing' : '')}
                        style={{ ...glassPop, width: 'min(200px, 58vw)', top: 'calc(88px + env(safe-area-inset-top))', transformOrigin: 'calc(100% - 67px) -17px' }}>
                        <div className="cr-views">
                            <div className="cr-view cr-view-list">
                                <div className={menuPhase === 'anim' ? 'cr-item-in' : ''}
                                    onClick={() => { setShowMenu(false); setShowSettings(true); }}
                                    style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 11px', borderRadius: 13, cursor: 'pointer', color: INK, animationDelay: '16ms' }}>
                                    <span style={{ flexShrink: 0, opacity: 0.85, display: 'flex' }}><svg width="17" height="17" viewBox="0 0 17 17" fill="none"><circle cx="8.5" cy="8.5" r="2.5" stroke="currentColor" strokeWidth="1.4"/><path d="M8.5 2v2M8.5 13v2M2 8.5h2M13 8.5h2M4.3 4.3l1.4 1.4M11.3 11.3l1.4 1.4M4.3 12.7l1.4-1.4M11.3 5.7l1.4-1.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg></span>
                                    <span style={{ fontSize: 14, flex: 1, letterSpacing: '-0.01em' }}>设置</span>
                                    <span style={{ fontSize: 12, color: INK2 }}>{humanName} / {aiName}</span>
                                </div>
                                <div style={{ height: '0.5px', background: 'hsla(40,8%,16%,0.10)', margin: '2px 11px' }} />
                                {([
                                    { key: 'records' as const, label: '阅读统计', icon: <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><circle cx="8.5" cy="8.5" r="6.5" stroke="currentColor" strokeWidth="1.4"/><path d="M8.5 5v3.5l2.4 1.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg> },
                                    { key: 'manage' as const, label: '管理书籍', icon: <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><rect x="2.5" y="3" width="12" height="3" rx="1" stroke="currentColor" strokeWidth="1.4"/><rect x="2.5" y="7.5" width="12" height="3" rx="1" stroke="currentColor" strokeWidth="1.4"/><rect x="2.5" y="12" width="12" height="3" rx="1" stroke="currentColor" strokeWidth="1.4"/></svg> },
                                    { key: 'backup' as const, label: '备份与恢复', icon: <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><path d="M4.5 12.5a3 3 0 01-.6-5.94A4.2 4.2 0 0112.3 7a2.8 2.8 0 01-.3 5.5H4.5z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/><path d="M8.5 8.5v4M8.5 8.5L6.8 10.2M8.5 8.5l1.7 1.7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg> },
                                ]).map((item, i) => (
                                    <div key={item.key} className={menuPhase === 'anim' ? 'cr-item-in' : ''}
                                        onClick={() => { if (item.key === 'records') { setShowMenu(false); openStatsDrawer('global'); } else openMenuDetail(item.key); }}
                                        style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 11px', borderRadius: 13, cursor: 'pointer', color: INK, animationDelay: `${40 + i * 24}ms` }}>
                                        <span style={{ flexShrink: 0, opacity: 0.85, display: 'flex' }}>{item.icon}</span>
                                        <span style={{ fontSize: 14, flex: 1, letterSpacing: '-0.01em' }}>{item.label}</span>
                                        <span style={{ fontSize: 14, color: INK2, fontWeight: 600 }}>›</span>
                                    </div>
                                ))}
                                {/* 统计版本切换胶囊 */}
                                <div style={{ padding: '6px 11px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}
                                    className={menuPhase === 'anim' ? 'cr-item-in' : ''}
                                    onClick={() => { const nv = statsView === 'shen' ? 'mizunashi' : 'shen'; setStatsView(nv); try { localStorage.setItem('coread-stats-view', nv); } catch {} }} >
                                    <span style={{ fontSize: 12, color: statsView === 'shen' ? INK : INK2, fontWeight: statsView === 'shen' ? 600 : 400, transition: 'color 200ms', cursor: 'pointer' }}>青瓷</span>
                                    <div style={{ position: 'relative', width: 40, height: 22, borderRadius: 11, background: statsView === 'shen' ? INK : 'rgba(60,55,45,0.2)', cursor: 'pointer', transition: 'background 200ms ease', flexShrink: 0 }}>
                                        <div style={{ position: 'absolute', top: 2, left: statsView === 'shen' ? 20 : 2, width: 18, height: 18, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.2)', transition: 'left 200ms ease' }} />
                                    </div>
                                    <span style={{ fontSize: 12, color: statsView === 'mizunashi' ? INK : INK2, fontWeight: statsView === 'mizunashi' ? 600 : 400, transition: 'color 200ms', cursor: 'pointer' }}>水無月</span>
                                </div>
                            </div>
                            <div className="cr-view cr-view-detail">
                                {menuDetail === 'manage' && (
                                    <>
                                        <div onClick={closeMenuDetail} style={vdHead}>
                                            <svg width="15" height="15" viewBox="0 0 15 15" fill="none" style={{ opacity: 0.85 }}><path d="M9.5 3L5 7.5L9.5 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
                                            <span style={{ fontSize: 14, fontWeight: 600, flex: 1, letterSpacing: '-0.01em' }}>管理书籍</span>
                                        </div>
                                        <div style={{ height: '0.5px', background: 'hsla(40,8%,16%,0.10)', margin: '4px 11px' }} />
                                        {([
                                            { key: 'delete' as const, label: '批量删除', desc: '选择书籍从书架移除' },
                                            { key: 'cache' as const, label: '清除分页缓存', desc: '选择书籍清除阅读缓存' },
                                        ]).map(item => (
                                            <div key={item.key} onClick={() => { setShowMenu(false); setManageAction(item.key); setEditMode(true); setSelectedBooks(new Set()); }}
                                                style={{ padding: '7px 11px', cursor: 'pointer' }}>
                                                <div style={{ fontSize: 13, color: INK }}>{item.label}</div>
                                                <div style={{ fontSize: 11, color: INK2, marginTop: 1 }}>{item.desc}</div>
                                            </div>
                                        ))}
                                        <div onClick={toggleDoor} style={{ padding: '7px 11px', cursor: 'pointer' }}>
                                            <div style={{ fontSize: 13, color: doorLocked ? '#16a34a' : '#ea580c' }}>{doorLocked ? '开门（解除锁定）' : '关门上锁'}</div>
                                            <div style={{ fontSize: 11, color: INK2, marginTop: 1 }}>{doorLocked ? `当前已关门：${aiName}读不了书` : `锁上后${aiName}无法开门读书，只有你能开`}</div>
                                        </div>
                                    </>
                                )}
                                {menuDetail === 'backup' && (
                                    <>
                                        <div onClick={closeMenuDetail} style={vdHead}>
                                            <svg width="15" height="15" viewBox="0 0 15 15" fill="none" style={{ opacity: 0.85 }}><path d="M9.5 3L5 7.5L9.5 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
                                            <span style={{ fontSize: 14, fontWeight: 600, flex: 1, letterSpacing: '-0.01em' }}>备份与恢复</span>
                                        </div>
                                        <div style={{ height: '0.5px', background: 'hsla(40,8%,16%,0.10)', margin: '4px 11px' }} />
                                        <div onClick={doBackupExport} style={{ padding: '7px 11px', cursor: 'pointer', opacity: backupBusy ? 0.5 : 1 }}>
                                            <div style={{ fontSize: 13, color: INK }}>{backupBusy ? '处理中…' : '导出备份'}</div>
                                            <div style={{ fontSize: 11, color: INK2, marginTop: 1 }}>书库+批注+进度+图片打包成文件</div>
                                        </div>
                                        <div onClick={doBackupList} style={{ padding: '7px 11px', cursor: 'pointer', opacity: backupBusy ? 0.5 : 1 }}>
                                            <div style={{ fontSize: 13, color: INK }}>恢复备份</div>
                                            <div style={{ fontSize: 11, color: INK2, marginTop: 1 }}>从已有备份恢复整个书库</div>
                                        </div>
                                        {backupFiles !== null && (
                                            <div style={{ margin: '0 11px 4px', padding: '4px 8px', borderRadius: 10, background: 'rgba(255,255,255,0.4)' }}>
                                                {backupFiles.length === 0 && <div style={{ fontSize: 12, color: INK2, padding: '6px 0' }}>服务器上还没有备份，先点上面导出一份</div>}
                                                {backupFiles.map(f => (
                                                    <div key={f.file} style={{ display: 'flex', alignItems: 'center', borderBottom: '0.5px solid hsla(40,8%,16%,0.10)' }}>
                                                        <div onClick={() => doBackupPreview({ file: f.file }, fmtBackupTime(f.mtime))}
                                                            style={{ flex: 1, minWidth: 0, fontSize: 12, color: INK, padding: '6px 0', cursor: 'pointer', overflowWrap: 'anywhere' }}>
                                                            {fmtBackupTime(f.mtime)} · {(f.bytes / 1048576).toFixed(0)}MB
                                                        </div>
                                                        <div onClick={async () => {
                                                            try { await api.backupDelete(f.file); } catch {}
                                                            setBackupFiles(prev => prev ? prev.filter(x => x.file !== f.file) : prev);
                                                        }} style={{ flexShrink: 0, padding: '4px 6px', fontSize: 13, color: INK2, cursor: 'pointer' }}>×</div>
                                                    </div>
                                                ))}
                                                <div onClick={() => restoreFileRef.current?.click()} style={{ fontSize: 12, color: INK, padding: '6px 0', cursor: 'pointer' }}>从本机选择备份文件…</div>
                                            </div>
                                        )}
                                    </>
                                )}
                            </div>
                        </div>
                    </div>
                </>
            )}

            {/* + 上传面板：同款玻璃弹窗 */}
            {panelPhase !== 'closed' && (
                <>
                    <div style={{ position: 'absolute', inset: 0, zIndex: 19 }}
                        onClick={() => { if (!uploading) setShowUpload(false); }} />
                    <div ref={panelRef}
                        className={'hy-shelf ' + (panelPhase === 'anim' ? 'cr-pop-anim' : panelPhase === 'closing' ? 'cr-pop-closing' : '')}
                        style={{ ...glassPop, width: 'min(240px, 68vw)', top: 'calc(50px + env(safe-area-inset-top))', transformOrigin: 'calc(100% - 19px) 21px' }}>
                        <div className="cr-views">
                            <div className="cr-view cr-view-list">
                                {([
                                    { key: 'file' as const, label: '上传文件', sub: 'PDF / TXT / EPUB', icon: <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><path d="M8.5 11V4M8.5 4L5.8 6.7M8.5 4l2.7 2.7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/><path d="M3 11.5v1.2a1.8 1.8 0 001.8 1.8h7.4a1.8 1.8 0 001.8-1.8v-1.2" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg> },
                                    { key: 'text' as const, label: '粘贴文本成书', sub: '', icon: <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><rect x="3.5" y="3" width="10" height="12" rx="2" stroke="currentColor" strokeWidth="1.4"/><path d="M6.2 3.2a2.3 2.3 0 014.6 0" stroke="currentColor" strokeWidth="1.4"/><path d="M6 8h5M6 11h3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg> },
                                    { key: 'batch' as const, label: '批量上传 EPUB', sub: '', icon: <svg width="17" height="17" viewBox="0 0 17 17" fill="none"><path d="M8.5 2.8L14.5 6l-6 3.2L2.5 6l6-3.2z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/><path d="M2.5 9.2l6 3.2 6-3.2M2.5 12.4l6 3.2 6-3.2" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/></svg> },
                                ]).map((item, i) => (
                                    <div key={item.key} className={panelPhase === 'anim' ? 'cr-item-in' : ''}
                                        onClick={() => { if (item.key === 'batch') batchFileRef.current?.click(); else openPanelDetail(item.key); }}
                                        style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '9px 11px', borderRadius: 13, cursor: 'pointer', color: INK, animationDelay: `${40 + i * 24}ms` }}>
                                        <span style={{ flexShrink: 0, opacity: 0.85, display: 'flex' }}>{item.icon}</span>
                                        <span style={{ fontSize: 14, flex: 1, letterSpacing: '-0.01em' }}>{item.label}</span>
                                        {item.sub && <span style={{ fontSize: 11, color: INK2 }}>{item.sub}</span>}
                                    </div>
                                ))}
                            </div>
                            <div className="cr-view cr-view-detail" />
                        </div>
                        <input ref={batchFileRef} type="file" accept=".epub,.pdf,.txt,.md" multiple onChange={handleBatchUpload} style={{ display: 'none' }} />
                    </div>
                </>
            )}

            {/* 上传详情独立玻璃窗 */}
            {panelDetail && (
                <>
                    <div onClick={closePanelDetail} style={{
                        position: 'absolute', inset: 0, zIndex: 30,
                        background: 'rgba(30,26,20,0.24)', backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)' as any,
                    }} />
                    <div className="hy-shelf cr-rise-in" onClick={(e) => e.stopPropagation()} style={{
                        position: 'absolute', left: 12, right: 12, top: 'calc(92px + env(safe-area-inset-top))', zIndex: 31,
                        maxHeight: 'calc(100% - 116px - env(safe-area-inset-top))', overflowY: 'auto', boxSizing: 'border-box',
                        borderRadius: 24, padding: '12px 5px 14px',
                        background: 'linear-gradient(rgba(255,255,255,0.26), rgba(255,255,255,0) 42%), rgba(252,252,252,0.62)',
                        backdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))', WebkitBackdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))' as any,
                        boxShadow: 'var(--hyalite-edge, 0 0 0 0 transparent), 0 18px 52px rgba(60,55,45,0.18), inset 0 1px 0 rgba(255,255,255,0.96)',
                        border: '1px solid rgba(255,255,255,0.82)',
                    }}>
                        <div onClick={closePanelDetail} style={{ ...vdHead, padding: '8px 11px 12px' }}>
                            <svg width="15" height="15" viewBox="0 0 15 15" fill="none" style={{ opacity: 0.85 }}><path d="M9.5 3L5 7.5L9.5 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
                            <span style={{ fontSize: 15, fontWeight: 700, flex: 1, letterSpacing: '-0.01em' }}>
                                {panelDetail === 'file' ? '上传文件' : '粘贴文本成书'}
                            </span>
                        </div>
                        {panelDetail === 'file' ? (
                            <>
                                <input value={uploadTitle} onChange={e => setUploadTitle(e.target.value)} placeholder="书名（可选，默认取文件名）" style={{ ...vdInput, fontSize: 14, padding: '11px 12px' }} />
                                <input ref={fileInputRef} type="file" accept=".pdf,.txt,.md,.epub" onChange={handleFileSelect} style={{ display: 'none' }} />
                                <div onClick={() => fileInputRef.current?.click()} style={{ ...vdFile, padding: '13px 12px', fontSize: 13 }}>
                                    {uploadFileName ? `已选: ${uploadFileName}` : '选择文件（PDF / TXT / EPUB）'}
                                </div>
                                <button onClick={handleUpload} disabled={uploading} style={{ ...vdCta, opacity: uploading ? 0.6 : 1 }}>
                                    {uploading ? '上传中...' : '添加到书架'}
                                </button>
                            </>
                        ) : (
                            <>
                                <input value={uploadTitle} onChange={e => setUploadTitle(e.target.value)} placeholder="书名" style={{ ...vdInput, fontSize: 14, padding: '11px 12px' }} />
                                <textarea value={uploadText} onChange={e => { setUploadText(e.target.value); setUploadFile(null); setUploadFileName(''); }}
                                    placeholder="粘贴文本内容...（段落之间用空行分隔）"
                                    style={{ ...vdInput, minHeight: 96, resize: 'vertical' as any, lineHeight: 1.5 }} />
                                <button onClick={handleUpload} disabled={uploading} style={{ ...vdCta, opacity: uploading ? 0.6 : 1 }}>
                                    {uploading ? '上传中...' : '添加到书架'}
                                </button>
                            </>
                        )}
                    </div>
                </>
            )}

            {/* Settings overlay */}
            {showJournal && <ReadingJournal stats={readingStats} loading={readingStatsLoading} dark={readerNightMode} close={() => setShowJournal(false)} />}
            {showSettings && (
                <>
                    <div onClick={() => setShowSettings(false)} style={{ position: 'absolute', inset: 0, zIndex: 30, background: 'rgba(30,26,20,0.24)', backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)' as any }} />
                    <div className="hy-shelf cr-rise-in" onClick={e => e.stopPropagation()} style={{
                        position: 'absolute', left: 12, right: 12, top: 'calc(92px + env(safe-area-inset-top))', zIndex: 31, boxSizing: 'border-box',
                        borderRadius: 24, padding: '18px 18px 14px',
                        background: 'linear-gradient(rgba(255,255,255,0.26), rgba(255,255,255,0) 42%), rgba(252,252,252,0.62)',
                        backdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))', WebkitBackdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))' as any,
                        boxShadow: 'var(--hyalite-edge, 0 0 0 0 transparent), 0 18px 52px rgba(60,55,45,0.18), inset 0 1px 0 rgba(255,255,255,0.96)',
                        border: '1px solid rgba(255,255,255,0.82)',
                    }}>
                        <div style={{ fontSize: 15, fontWeight: 700, color: INK, marginBottom: 18, letterSpacing: '-0.01em' }}>设置</div>
                        <label style={{ fontSize: 12, color: INK2, display: 'block', marginBottom: 6 }}>我的名字</label>
                        <input value={humanName} onChange={e => { setHumanName(e.target.value); localStorage.setItem('coread-human-name', e.target.value); }}
                            style={{ ...vdInput, margin: '0 0 12px' }} />
                        <label style={{ fontSize: 12, color: INK2, display: 'block', marginBottom: 6 }}>{COREAD_PERSONA_NAME ? 'AI的名字（当前人设）' : 'AI的名字'}</label>
                        <input value={aiName} readOnly={Boolean(COREAD_PERSONA_NAME)} onChange={e => { setAiName(e.target.value); localStorage.setItem('coread-ai-name', e.target.value); }}
                            style={{ ...vdInput, margin: '0 0 12px', ...(COREAD_PERSONA_NAME ? { opacity: 0.65, cursor: 'default' } : {}) }} />
                        <button onClick={() => setShowSettings(false)} style={{ ...vdCta, margin: '8px 0 0' }}>完成</button>
                    </div>
                </>
            )}

            {/* Delete confirmation — glass */}
            {confirmDelete !== null && glassConfirm({
                title: '确认删除？',
                desc: '书籍和所有批注都会被删除',
                confirmText: '删除',
                onConfirm: () => handleDeleteBook(confirmDelete),
                onCancel: () => setConfirmDelete(null),
            })}
            {confirmBatchDelete && glassConfirm({
                title: `删除选中的 ${selectedBooks.size} 本书？`,
                desc: '书籍和所有批注都会被删除，不可恢复',
                confirmText: '删除',
                onConfirm: async () => {
                    setConfirmBatchDelete(false);
                    for (const id of selectedBooks) {
                        try { await fetch(`${BASE}/v1/books/${id}`, { method: 'DELETE', headers: { 'x-coread-key': COREAD_ACCESS_KEY } }); } catch {}
                    }
                    setSelectedBooks(new Set()); setEditMode(false); setManageAction(null); loadBooks();
                    toast(`已删除 ${selectedBooks.size} 本`);
                },
                onCancel: () => setConfirmBatchDelete(false),
            })}
            {restorePreview && glassConfirm({
                title: '恢复这个备份？',
                desc: `${restorePreview.label}：${restorePreview.counts.books} 本书 · ${restorePreview.counts.comments} 条批注 · ${Math.round((restorePreview.counts.readingSeconds || 0) / 60)} 分钟记录。现有书库会被替换成备份内容。`,
                confirmText: '恢复',
                onConfirm: doBackupRestore,
                onCancel: () => setRestorePreview(null),
            })}

            {/* TOC overlay */}
            {showToc && (() => {
                const tInk = readerNightMode ? '#ddd' : INK;
                const tInk2 = readerNightMode ? '#888' : INK2;
                const tPaper = readerNightMode ? '#242424' : '#fffcf3';
                const tBorder = readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.10)';
                const closeToc = () => { setTocPhase('closing'); setTimeout(() => { setShowToc(false); setTocPhase('closed'); }, 240); };
                return (
                <>
                    <div className={tocPhase === 'closing' ? 'cr-scrim-out' : 'cr-scrim-in'}
                        onClick={closeToc}
                        style={{ position: 'absolute', inset: 0, zIndex: 30, background: readerNightMode ? 'rgba(0,0,0,0.5)' : 'rgba(30,26,20,0.35)' }} />
                    <div onClick={(e) => e.stopPropagation()}
                        className={tocPhase === 'closing' ? 'cr-sheet-out' : 'cr-sheet-in'}
                        style={{
                            position: 'absolute', left: 0, right: 0, bottom: 0, top: 56, zIndex: 31,
                            borderRadius: '24px 24px 0 0', background: tPaper,
                            boxShadow: readerNightMode ? '0 -8px 40px rgba(0,0,0,0.5)' : '0 -8px 40px rgba(40,35,25,0.18)',
                            display: 'flex', flexDirection: 'column', overflow: 'hidden',
                        }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '16px 18px 12px', borderBottom: `1px solid ${tBorder}`, flexShrink: 0 }}>
                            <div style={{ width: 34, height: 46, borderRadius: 6, flexShrink: 0, overflow: 'hidden', background: c.primaryBg, border: `1px solid ${tBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 700, color: tInk2 }}>
                                {activeBook?.cover_image
                                    ? <img src={api.imageUrl(activeBook.id, activeBook.cover_image)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                    : (activeBook?.title || '书').charAt(0)}
                            </div>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 15, fontWeight: 700, color: tInk, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{activeBook?.title || '目录'}</div>
                                <div style={{ fontSize: 12, color: tInk2, marginTop: 2 }}>第 {spread && page < totalPages ? `${page}-${page + 1}` : page} 页 · 共 {totalPages} 页</div>
                            </div>
                            <button onClick={closeToc} style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, background: readerNightMode ? 'rgba(255,255,255,0.10)' : 'rgba(60,55,45,0.08)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', color: tInk2, cursor: 'pointer' }}>
                                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
                            </button>
                        </div>
                        <div ref={tocListRef} onScroll={(e) => setTocScrollTop((e.target as HTMLDivElement).scrollTop)}
                            style={{ flex: 1, overflow: 'auto' }} className="no-scrollbar">
                        {(() => {
                            const viewH = tocViewH || 400;
                            const winStart = Math.max(0, Math.floor(tocScrollTop / TOC_ROW_H) - 8);
                            const winEnd = Math.min(tocChapters.length, Math.ceil((tocScrollTop + viewH) / TOC_ROW_H) + 8);
                            const rows = [];
                            for (let i = winStart; i < winEnd; i++) {
                                const ch = tocChapters[i];
                                const pg = findPageForParaIdx(ch.idx ?? ch.page);
                                const chPage = pg >= 0 ? pg + 1 : ch.page;
                                const isCurrent = i === currentChapterIdx;
                                rows.push(
                                    <button key={i} onClick={() => jumpToChapter(ch)} style={{
                                        position: 'absolute', top: i * TOC_ROW_H, left: 0, right: 0, height: TOC_ROW_H,
                                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                        padding: '0 18px', background: isCurrent ? (readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.07)') : 'transparent',
                                        border: 'none', cursor: 'pointer', textAlign: 'left',
                                    }}>
                                        <span style={{ fontSize: 13, color: isCurrent ? tInk : (readerNightMode ? '#aaa' : 'hsl(40,6%,34%)'), fontWeight: isCurrent ? 700 : 400, flex: 1, lineHeight: 1.4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{ch.title}</span>
                                        <span style={{ fontSize: 11, color: tInk2, marginLeft: 8, flexShrink: 0 }}>p.{chPage}</span>
                                    </button>
                                );
                            }
                            return <div style={{ position: 'relative', height: tocChapters.length * TOC_ROW_H }}>{rows}</div>;
                        })()}
                        </div>
                    </div>
                </>
                );
            })()}

            {/* 所有批注抽屉 */}
            {allNotesPhase !== 'closed' && (() => {
                const nInk = readerNightMode ? '#ddd' : INK;
                const nInk2 = readerNightMode ? '#888' : INK2;
                const nPaper = readerNightMode ? '#242424' : '#fffcf3';
                const nBorder = readerNightMode ? 'rgba(255,255,255,0.08)' : 'rgba(60,55,45,0.10)';
                const closeAllNotes = () => { setAllNotesPhase('closing'); setTimeout(() => setAllNotesPhase(p => p === 'closing' ? 'closed' : p), 240); };
                const sorted = [...allComments].filter(cm => !cm.reply_to).sort((a, b) => a.paragraph_idx - b.paragraph_idx);
                const fmtTime = (ts: string) => { const d = new Date(ts); const mm = String(d.getMonth() + 1).padStart(2, '0'); const dd = String(d.getDate()).padStart(2, '0'); return `${mm}-${dd}`; };
                return (
                <>
                    <div className={allNotesPhase === 'closing' ? 'cr-scrim-out' : 'cr-scrim-in'}
                        onClick={closeAllNotes}
                        style={{ position: 'absolute', inset: 0, zIndex: 30, background: readerNightMode ? 'rgba(0,0,0,0.5)' : 'rgba(30,26,20,0.35)' }} />
                    <div onClick={(e) => e.stopPropagation()}
                        className={allNotesPhase === 'closing' ? 'cr-sheet-out' : 'cr-sheet-in'}
                        style={{
                            position: 'absolute', left: 0, right: 0, bottom: 0, top: 56, zIndex: 31,
                            borderRadius: '24px 24px 0 0', background: nPaper,
                            boxShadow: readerNightMode ? '0 -8px 40px rgba(0,0,0,0.5)' : '0 -8px 40px rgba(40,35,25,0.18)',
                            display: 'flex', flexDirection: 'column', overflow: 'hidden',
                        }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '16px 18px 12px', borderBottom: `1px solid ${nBorder}`, flexShrink: 0 }}>
                            <div style={{ width: 34, height: 46, borderRadius: 6, flexShrink: 0, overflow: 'hidden', background: c.primaryBg, border: `1px solid ${nBorder}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, fontWeight: 700, color: nInk2 }}>
                                {activeBook?.cover_image
                                    ? <img src={api.imageUrl(activeBook.id, activeBook.cover_image)} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                    : (activeBook?.title || '书').charAt(0)}
                            </div>
                            <span style={{ fontSize: 15, fontWeight: 700, color: nInk, flex: 1 }}>全部批注<span style={{ fontSize: 12, fontWeight: 400, color: nInk2, marginLeft: 6 }}>{sorted.length} 条</span></span>
                            <button onClick={closeAllNotes} style={{ width: 28, height: 28, borderRadius: '50%', flexShrink: 0, background: readerNightMode ? 'rgba(255,255,255,0.10)' : 'rgba(60,55,45,0.08)', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', color: nInk2, cursor: 'pointer' }}>
                                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
                            </button>
                        </div>
                        <div style={{ flex: 1, overflow: 'auto', padding: '6px 0 20px' }} className="no-scrollbar">
                            {sorted.length === 0 ? (
                                <div style={{ padding: '28px 18px', fontSize: 13, color: nInk2, textAlign: 'center' }}>这本书还没有批注</div>
                            ) : sorted.map(cm => (
                                <div key={cm.id}
                                    onClick={() => { rememberReturnPoint(); closeAllNotes(); const pg = findPageForParaIdx(cm.paragraph_idx); if (pg >= 0) setPage(pg + 1); }}
                                    style={{ padding: '10px 18px', cursor: 'pointer', borderBottom: `1px solid ${readerNightMode ? 'rgba(255,255,255,0.05)' : 'rgba(60,55,45,0.06)'}` }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                                        <span style={{ width: 22, height: 22, borderRadius: '50%', background: readerNightMode ? 'rgba(255,255,255,0.12)' : 'rgba(60,55,45,0.10)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 600, color: nInk2 }}>{displayName(cm.from_who).charAt(0)}</span>
                                        <span style={{ fontSize: 12, color: nInk2 }}>{displayName(cm.from_who)} · 批注</span>
                                        <span style={{ fontSize: 11, color: nInk2, marginLeft: 'auto' }}>{fmtTime(cm.created_at)}</span>
                                    </div>
                                    {cm.selected_text && (
                                        <div style={{ fontSize: 12, color: nInk2, marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cm.selected_text}</div>
                                    )}
                                    <div style={{ fontSize: 13, color: nInk, lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical' as any, overflow: 'hidden' }}>{cm.content}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                </>
                );
            })()}

            {lockPhase !== 'closed' && (() => {
                const lNight = readerNightMode;
                const lockRed = lNight ? 'hsl(9,50%,50%)' : 'hsl(9,52%,44%)';
                const badgeRed = lNight ? 'hsl(9,60%,62%)' : lockRed;
                return (
                <>
                    <div className={lockPhase === 'closing' ? 'cr-scrim-out' : 'cr-scrim-in'}
                        onClick={closeLockDrawer}
                        style={{ position: 'absolute', inset: 0, zIndex: 40, background: lNight ? 'rgba(0,0,0,0.4)' : 'rgba(30,26,20,0.24)', backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)' }} />
                    <div onClick={(e) => e.stopPropagation()}
                        className={'hy-shelf ' + (lockPhase === 'closing' ? 'cr-sheet-out' : 'cr-sheet-in')}
                        style={{
                            position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 41,
                            borderRadius: '24px 24px 0 0',
                            background: lNight
                                ? 'linear-gradient(rgba(255,255,255,0.10), rgba(255,255,255,0) 42%), rgba(38,38,38,0.66)'
                                : 'linear-gradient(rgba(255,255,255,0.26), rgba(255,255,255,0) 42%), rgba(252,252,252,0.62)',
                            backdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))', WebkitBackdropFilter: 'var(--hyalite, blur(48px) saturate(1.8))',
                            boxShadow: lNight
                                ? '0 -18px 52px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.14)'
                                : 'var(--hyalite-edge, 0 0 0 0 transparent), 0 -18px 52px rgba(60,55,45,0.18), inset 0 1px 0 rgba(255,255,255,0.96)',
                            border: lNight ? '1px solid rgba(255,255,255,0.14)' : '1px solid rgba(255,255,255,0.82)',
                            borderBottom: 'none',
                            padding: '28px 24px calc(24px + env(safe-area-inset-bottom))', textAlign: 'center',
                        }}>
                        <div style={{ width: 58, height: 58, borderRadius: '50%', margin: '0 auto 14px', background: lNight ? 'hsla(9,50%,50%,0.14)' : 'hsla(9,52%,44%,0.10)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: badgeRed }}>
                            <svg width="26" height="26" viewBox="0 0 26 26" fill="none"><rect x="5.5" y="11" width="15" height="10.5" rx="3" stroke="currentColor" strokeWidth="1.8"/><path d="M8.5 11V8.2a4.5 4.5 0 019 0V11" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/><circle cx="13" cy="16.2" r="1.6" fill="currentColor"/></svg>
                        </div>
                        <div style={{ fontSize: 17, fontWeight: 700, color: lNight ? '#ddd' : INK, marginBottom: 8 }}>{lockIntent ? '关门上锁？' : '开门？'}</div>
                        <div style={{ fontSize: 13, color: lNight ? '#888' : INK2, lineHeight: 1.6, marginBottom: 22 }}>{lockIntent ? `锁上后${aiName}无法开门读书，只有你能开门。` : `${aiName}就能继续读书了。`}</div>
                        <button onClick={confirmDoor} style={{ display: 'block', width: '100%', padding: '13px 0', borderRadius: 999, border: 'none', background: lockRed, color: '#fff', fontSize: 15, fontWeight: 600, cursor: 'pointer', marginBottom: 10 }}>
                            {lockIntent ? '关门上锁' : '开门'}
                        </button>
                        <button onClick={closeLockDrawer} style={{ display: 'block', width: '100%', padding: '13px 0', borderRadius: 999, border: 'none', background: 'transparent', color: lNight ? '#888' : INK2, fontSize: 15, fontWeight: 500, cursor: 'pointer' }}>
                            取消
                        </button>
                    </div>
                </>
                );
            })()}
        </div>
    );
};

export default StudyApp;
