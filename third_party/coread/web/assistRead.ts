export type AssistKind = 'content' | 'function' | 'punct';
export interface AssistToken { text: string; kind: AssistKind }

export interface AssistSegmenter { segment(input: string): Iterable<{ segment: string }> }

const FUNCTION_WORD_LIST = [
    // 助词、语气词
    '的', '地', '得', '了', '着', '过', '吗', '呢', '吧', '啊', '呀', '嘛', '哦', '哇', '啦', '么', '哈', '呗', '咯', '喽', '嘞', '罢了', '而已', '似的', '一样', '一般', '的话', '所', '之', '乎', '者', '也', '矣', '焉', '哉', '兮',
    '等', '等等', '们', '来着',
    // 介词
    '在', '于', '对', '把', '被', '从', '向', '往', '给', '跟', '和', '与', '同', '为', '因', '按', '照', '按照', '依照', '关于', '对于', '根据', '至于', '由', '由于', '自', '自从', '以', '以及', '经', '经过', '通过', '随着', '除', '除了', '除非', '比', '朝', '沿', '沿着', '为了', '依', '据', '凭', '趁', '当', '让', '叫', '将', '替', '为着', '鉴于', '基于', '针对', '面对', '有关', '至', '到',
    // 连词
    '而', '且', '而且', '并且', '并', '但', '但是', '可是', '可', '然而', '因为', '所以', '因此', '因而', '如果', '假如', '假使', '要是', '倘若', '若', '虽然', '虽', '虽说', '尽管', '然后', '接着', '或者', '或', '或是', '还是', '以及', '及', '不过', '只是', '只要', '只有', '无论', '不论', '不管', '即使', '即便', '哪怕', '既然', '既', '于是', '从而', '以便', '以免', '免得', '甚至', '何况', '况且', '否则', '不然', '要不', '要么', '与其', '不如', '宁可', '一旦', '一边', '同时', '此外', '另外', '总之', '那么', '那', '可见', '固然', '然', '则', '乃', '就是', '不但', '不仅', '而是', '再说',
    // 副词
    '很', '太', '最', '都', '也', '就', '才', '又', '还', '已', '已经', '曾', '曾经', '正', '正在', '在', '似乎', '好像', '仿佛', '目前', '非常', '比较', '相对', '越来越', '越', '更', '更加', '挺', '十分', '极', '极其', '特别', '尤其', '格外', '有点', '有些', '稍', '稍微', '略', '颇', '相当', '几乎', '差不多', '总', '总是', '常常', '经常', '往往', '一直', '一向', '始终', '永远', '向来', '从来', '刚', '刚刚', '才刚', '马上', '立刻', '立即', '随即', '终于', '到底', '究竟', '毕竟', '居然', '竟然', '竟', '果然', '其实', '确实', '的确', '实在', '真的', '简直', '反正', '偏', '偏偏', '只', '仅', '仅仅', '就要', '快要', '将要', '将', '不', '没', '没有', '别', '不要', '未', '无', '非', '莫', '勿', '甭', '也许', '或许', '大概', '大约', '恐怕', '可能', '必', '必须', '必定', '一定', '肯定', '难道', '何必', '何尝', '岂', '再', '再次', '又再', '还是', '仍', '仍然', '依然', '照样', '亦', '均', '皆', '全都', '一起', '一块', '一共', '总共', '一概', '统统', '渐渐', '逐渐', '慢慢', '忽然', '突然', '顿时', '当然', '自然', '本来', '原来', '其', '就此', '如此', '这么', '那么', '这样', '那样', '怎样', '如何', '多么', '够', '真是', '只好', '不得不', '赶紧', '连忙', '急忙', '暂且', '姑且', '也就', '便', '即', '即将', '遂', '方才', '早已', '早就', '已然', '尚', '尚未', '还未', '先', '首先', '最后', '然后', '后来', '原先', '起初', '当时', '此时', '这时', '那时', '现在', '如今', '一下', '一点', '有点儿', '一些', '些',
    // 代词
    '我', '你', '您', '他', '她', '它', '我们', '你们', '他们', '她们', '它们', '咱', '咱们', '自己', '自身', '本人', '大家', '人家', '别人', '他人', '彼此', '这', '那', '这个', '那个', '这些', '那些', '这里', '那里', '这儿', '那儿', '这边', '那边', '此', '彼', '该', '本', '其', '其他', '其它', '其余', '什么', '怎么', '怎么样', '为什么', '哪', '哪个', '哪些', '哪里', '哪儿', '谁', '多少', '几', '每', '各', '各自', '某', '某些', '任何', '一切', '所有', '凡', '吾', '汝', '尔', '之', '其中', '这种', '那种', '这样的', '那样的', '这次', '那次', '这时候', '那时候',
    // 量词与数量结构
    '个', '一个', '一种', '种', '一些', '些', '一样', '一次', '次', '一下', '件', '一件', '位', '一位', '只', '一只', '把', '本', '一本', '一点', '番', '一番', '遍', '一遍', '阵', '一阵', '一切', '一',
    // 判断、存在、能愿
    '是', '有', '没', '为', '乃', '系', '即', '能', '会', '可以', '可', '要', '应', '应该', '应当', '该', '得', '肯', '敢', '愿', '愿意', '能够', '可能', '必须', '不能', '不会', '不是', '没有', '就是', '还有', '只是', '便是', '正是', '也是', '都是', '还是', '算是', '像', '如', '似', '使', '令', '让', '叫',
    // 方位、时体
    '上', '里', '中', '内', '外', '前', '后', '间', '之间', '之中', '之后', '之前', '之下', '之上', '以上', '以下', '以内', '以外', '以后', '以前', '时', '时候', '的时候', '当中', '里面', '上面', '下面', '前面', '后面', '旁边',
];

export const FUNCTION_WORDS: ReadonlySet<string> = new Set(FUNCTION_WORD_LIST);

const HAS_WORD_CHAR = /[\p{L}\p{N}]/u;

export function classifyToken(text: string): AssistKind {
    if (!HAS_WORD_CHAR.test(text)) return 'punct';
    return FUNCTION_WORDS.has(text) ? 'function' : 'content';
}

export function createSegmenter(): AssistSegmenter | null {
    try {
        const Seg = (Intl as any).Segmenter;
        return typeof Seg === 'function' ? new Seg('zh', { granularity: 'word' }) : null;
    } catch { return null; }
}

export function splitAssistTokens(text: string, segmenter: AssistSegmenter): AssistToken[] {
    const out: AssistToken[] = [];
    for (const { segment } of segmenter.segment(text)) {
        if (!segment) continue;
        const kind = classifyToken(segment);
        const last = out[out.length - 1];
        // 相邻标点/空白并成一段，少挂 DOM 节点
        if (kind === 'punct' && last && last.kind === 'punct') last.text += segment;
        else out.push({ text: segment, kind });
    }
    return out;
}

export interface AssistOpts { split: boolean; bold: boolean; enlarge: boolean }
export interface PlacedToken extends AssistToken { start: number; gapAfter: boolean }

/** 整段切词后再截取 [from,to)：分页测量和页面显示都从整段切，保证两边词界一致 */
export function placeTokens(tokens: AssistToken[], from: number, to: number): PlacedToken[] {
    const out: PlacedToken[] = [];
    let pos = 0;
    for (let i = 0; i < tokens.length; i++) {
        const t = tokens[i];
        const s = pos, e = pos + t.text.length;
        pos = e;
        if (e <= from) continue;
        if (s >= to) break;
        const next = tokens[i + 1];
        const gapAfter = t.kind !== 'punct' && !!next && next.kind !== 'punct';
        const cs = Math.max(s, from), ce = Math.min(e, to);
        out.push({ text: t.text.slice(cs - s, ce - s), kind: t.kind, start: cs, gapAfter: gapAfter && ce === e });
    }
    return out;
}

export function assistActive(o: AssistOpts) { return o.split || o.bold || o.enlarge; }

const NOWRAP_OK = /^[㐀-鿿豈-﫿]{2,8}$/;

/** 单个词 span 的样式；measure 和 render 共用，任何会影响排版的属性只能在这里改 */
export function tokenStyle(kind: AssistKind, text: string, o: AssistOpts, gapPx: number, gapAfter: boolean, night: boolean): Record<string, string> | null {
    const st: Record<string, string> = {};
    if (kind === 'content') {
        if (o.enlarge) { st.fontSize = '1.12em'; st.fontWeight = '700'; }
        else if (o.bold) st.fontWeight = '700';
    } else if (kind === 'function' && o.enlarge) {
        st.fontSize = '0.88em';
        st.color = night ? '#8a8a8a' : '#9a958c';
    }
    if (o.split && kind !== 'punct') {
        if (gapAfter) st.marginRight = `${gapPx}px`;
        if (NOWRAP_OK.test(text)) st.whiteSpace = 'nowrap';
    }
    return Object.keys(st).length ? st : null;
}

export type SpacingLevel = 0 | 1 | 2;
const LH_ADD = [0, 0.35, 0.7];
const LS_EM = [0, 0.06, 0.12];

export function assistSpacing(baseLineHeight: number, baseLetterPx: number, fontPx: number, level: SpacingLevel) {
    return {
        lineHeight: +(baseLineHeight + LH_ADD[level]).toFixed(3),
        letterSpacing: level ? +(baseLetterPx + fontPx * LS_EM[level]).toFixed(2) : baseLetterPx,
    };
}
