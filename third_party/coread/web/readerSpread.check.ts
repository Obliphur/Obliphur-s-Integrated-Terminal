// 双页版式小检查：bun web/readerSpread.check.ts
import { readerColumns, type PageLayout } from './readerSpread';

let fail = 0, total = 0;
const eq = (name: string, got: unknown, want: unknown) => {
    total++;
    const g = JSON.stringify(got), w = JSON.stringify(want);
    if (g !== w) { fail++; console.log(`FAIL ${name}\n  got  ${g}\n  want ${w}`); }
};
const col = (layout: PageLayout, w: number, h: number, fs = 14) => readerColumns(layout, w, h, fs, 56, 72, 36);

eq('手机竖拿自动：单页铺满', col('auto', 390, 844), { spread: false, contentWidth: 334 });
eq('平板横拿自动：双页，每页36字封顶', col('auto', 1194, 834), { spread: true, contentWidth: 504 });
eq('电脑全屏自动：双页，每页36字封顶', col('auto', 1440, 900), { spread: true, contentWidth: 504 });
eq('平板竖拿自动：单页，36字封顶', col('auto', 834, 1194), { spread: false, contentWidth: 504 });
eq('手机横拿自动（不到900宽）：单页', col('auto', 844, 390), { spread: false, contentWidth: 788 });
eq('大平板竖拿自动（宽过900也不摊）：单页，36字封顶', col('auto', 1024, 1366), { spread: false, contentWidth: 504 });
eq('电脑手动单页：一行36字封顶', col('single', 1440, 900), { spread: false, contentWidth: 504 });
eq('平板竖拿手动双页：放得下就摊', col('double', 834, 1194), { spread: true, contentWidth: 325 });
eq('手机手动双页放不下：退回单页', col('double', 390, 844), { spread: false, contentWidth: 334 });
eq('字号调大后放不下22字：自动退回单页，按大字号36字封顶', col('auto', 1000, 700, 22), { spread: false, contentWidth: 792 });
eq('电脑窄窗口（短边不到600）单页：铺满', col('single', 500, 900), { spread: false, contentWidth: 444 });
eq('手机横拿手动单页：手机不收窄', col('single', 844, 390), { spread: false, contentWidth: 788 });

console.log(fail ? `${fail}/${total} FAIL` : `${total}/${total} ok`);
// 不用 process.exit：web/ 的 tsconfig 没有 node 类型，抛错一样让进程非零退出
if (fail) throw new Error(`readerSpread: ${fail}/${total} FAIL`);
