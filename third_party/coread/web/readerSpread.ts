export type PageLayout = 'auto' | 'single' | 'double';

/** pagePadX：单页左右内边距合计；平板和电脑上一行最多 maxChars 个字宽，单页也居中收窄，手机（短边不到600）不收 */
export function readerColumns(layout: PageLayout, width: number, height: number, fontSize: number, pagePadX: number, gutter: number, maxChars: number) {
    const avail = (width - 2 * pagePadX - gutter) / 2;
    const spread = layout === 'double' ? avail >= fontSize * 14
        : layout === 'auto' ? width >= 900 && width > height && avail >= fontSize * 22
        : false;
    const capSingle = Math.min(width, height) >= 600;
    const contentWidth = Math.max(1, Math.floor(
        spread ? Math.min(avail, fontSize * maxChars)
        : capSingle ? Math.min(width - pagePadX, fontSize * maxChars)
        : width - pagePadX));
    return { spread, contentWidth };
}
