// block：这一行所属段落在本页的序号（同一段落的本页部分同号）；不属于任何段落的行（图片等）不带
export interface LineRect { top: number; bottom: number; left: number; right: number; block?: number }

export function groupRectsIntoLines(rects: LineRect[]): LineRect[] {
    const sorted = rects
        .filter(r => r.right - r.left >= 1 && r.bottom - r.top >= 1)
        .sort((a, b) => a.top - b.top || a.left - b.left);
    const lines: LineRect[] = [];
    for (const r of sorted) {
        const last = lines[lines.length - 1];
        if (last) {
            const overlap = Math.min(last.bottom, r.bottom) - Math.max(last.top, r.top);
            if (overlap > 0.5 * Math.min(last.bottom - last.top, r.bottom - r.top)) {
                last.top = Math.min(last.top, r.top);
                last.bottom = Math.max(last.bottom, r.bottom);
                last.left = Math.min(last.left, r.left);
                last.right = Math.max(last.right, r.right);
                continue;
            }
        }
        lines.push({ ...r });
    }
    return lines;
}

// 文字区域含字体上下预留的空白，中文字在里面偏上；换成字本身的上下边，框才能把字摆正
const INK_SAMPLE = '国永中';
const inkCache = new Map<string, { top: number; bottom: number } | null>();
let inkCtx: CanvasRenderingContext2D | null | undefined;
// 画布的 fontBoundingBox 只算首选字体；中文常由后备字体画，文字区域按后备字体撑开，基线只能在页面里量
function domAscent(font: string): number {
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;visibility:hidden;white-space:nowrap';
    probe.style.font = font;
    probe.style.lineHeight = 'normal';
    const text = document.createTextNode(INK_SAMPLE);
    const mark = document.createElement('span');
    mark.style.cssText = 'display:inline-block;width:0;height:0';
    probe.append(text, mark);
    document.body.appendChild(probe);
    const range = document.createRange();
    range.selectNodeContents(text);
    const r = range.getClientRects()[0];
    const ascent = r ? mark.getBoundingClientRect().bottom - r.top : 0;
    probe.remove();
    return ascent;
}
function inkOffsets(el: Element | null): { top: number; bottom: number } | null {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    if (inkCache.has(font)) return inkCache.get(font)!;
    if (inkCtx === undefined) inkCtx = document.createElement('canvas').getContext('2d');
    let res: { top: number; bottom: number } | null = null;
    if (inkCtx) {
        inkCtx.font = font;
        const m = inkCtx.measureText(INK_SAMPLE);
        const ascent = domAscent(font);
        if (ascent > 0 && m.actualBoundingBoxAscent > 0) {
            res = { top: ascent - m.actualBoundingBoxAscent, bottom: ascent + m.actualBoundingBoxDescent };
        }
    }
    inkCache.set(font, res);
    return res;
}

/** blockSelector：段落元素的选择器；给了就把每行标上所属段落（逐段聚焦用），不给就和以前一样只量行 */
export function measurePageLines(root: HTMLElement, origin: HTMLElement, blockSelector?: string): LineRect[] {
    const o = origin.getBoundingClientRect();
    const maxBottom = o.top + origin.clientHeight;
    const rects: LineRect[] = [];
    const blockIds = new Map<Element, number>();
    const blockOf = (el: Element | null): number | undefined => {
        const b = blockSelector && el ? el.closest(blockSelector) : null;
        if (!b) return undefined;
        let id = blockIds.get(b);
        if (id === undefined) { id = blockIds.size; blockIds.set(b, id); }
        return id;
    };
    const push = (r: DOMRect, ink: { top: number; bottom: number } | null, block: number | undefined) => {
        if (r.bottom <= o.top || r.top >= maxBottom) return;
        const dy = origin.scrollTop - o.top, dx = origin.scrollLeft - o.left;
        const top = ink ? r.top + ink.top : r.top, bottom = ink ? r.top + ink.bottom : r.bottom;
        const rect: LineRect = { top: top + dy, bottom: bottom + dy, left: r.left + dx, right: r.right + dx };
        if (block !== undefined) rect.block = block;
        rects.push(rect);
    };
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
        acceptNode: n => n.nodeType === Node.TEXT_NODE
            ? (n.nodeValue && n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP)
            : ((n as Element).tagName === 'IMG' ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP),
    });
    const range = document.createRange();
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        if (n.nodeType === Node.TEXT_NODE) {
            range.selectNodeContents(n);
            const ink = inkOffsets(n.parentElement);
            const block = blockOf(n.parentElement);
            for (const r of Array.from(range.getClientRects())) push(r, ink, block);
        } else {
            push((n as Element).getBoundingClientRect(), null, blockOf(n as Element));
        }
    }
    range.detach?.();
    return groupRectsIntoLines(rects);
}

// 逐行 / 逐段：两种都用同一个行下标 idx 记位置，逐段时框住 idx 所在那一段，切换模式不丢位置
export type FocusUnit = 'line' | 'para';
export interface FocusBlock extends LineRect { first: number; last: number }

/** 按所属段落把行合并成框：相邻且同段的行并成一个外接框；没标段落的行（图片等）各自成块。
 *  一段跨页时 DOM 里本页只有它的本页部分，所以框的天然就是本页那几行 */
export function groupLinesIntoBlocks(lines: LineRect[]): FocusBlock[] {
    const out: FocusBlock[] = [];
    lines.forEach((ln, i) => {
        const last = out[out.length - 1];
        if (last && ln.block !== undefined && last.block === ln.block && last.last === i - 1) {
            last.top = Math.min(last.top, ln.top);
            last.bottom = Math.max(last.bottom, ln.bottom);
            last.left = Math.min(last.left, ln.left);
            last.right = Math.max(last.right, ln.right);
            last.last = i;
        } else {
            out.push({ top: ln.top, bottom: ln.bottom, left: ln.left, right: ln.right, ...(ln.block !== undefined ? { block: ln.block } : {}), first: i, last: i });
        }
    });
    return out;
}

function blockAt(blocks: FocusBlock[], idx: number): number {
    const k = blocks.findIndex(b => idx >= b.first && idx <= b.last);
    return k < 0 ? (idx < 0 ? 0 : blocks.length - 1) : k;
}

/** 当前该框住的范围（上下边）；没有行时 null */
export function focusRange(lines: LineRect[], idx: number, unit: FocusUnit): LineRect | null {
    if (!lines.length) return null;
    const i = Math.max(0, Math.min(idx, lines.length - 1));
    if (unit === 'line') return lines[i];
    const blocks = groupLinesIntoBlocks(lines);
    return blocks[blockAt(blocks, i)];
}

/** 走一步：返回新的行下标；走出本页返回 null，由调用方翻页（往下落新页第一行=第一段，往上落最后一行=最后一段） */
export function stepFocusIndex(lines: LineRect[], idx: number, delta: 1 | -1, unit: FocusUnit): number | null {
    if (unit === 'line') {
        const next = idx + delta;
        return next >= 0 && next < lines.length ? next : null;
    }
    const blocks = groupLinesIntoBlocks(lines);
    if (!blocks.length) return null;
    const nb = blockAt(blocks, Math.max(0, Math.min(idx, lines.length - 1))) + delta;
    return nb >= 0 && nb < blocks.length ? blocks[nb].first : null;
}
