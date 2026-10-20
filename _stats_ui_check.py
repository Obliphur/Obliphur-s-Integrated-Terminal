import re
import tkinter as tk

import config
config.UI_SCALE = 1.0
import contract as C
from data_manager import DataManager
from pages.stats_page import StatsPage, CONTRACT_ROWS, _level_text

root = tk.Tk()
root.withdraw()
dm = DataManager()


class _Net:
    pass


page = StatsPage(root, dm, _Net())
page.pack(fill="both", expand=True)


def rec(i, result="completed", orig=("long3", "tired3", "silent", "music"), terms=("long3", "tired2", "silent")):
    return {"id": i, "end": f"2026-10-{i:02d}T10:00:00", "result": result,
            "orig": list(orig), "terms": list(terms), "orig_level": i + 2, "level": i}


# 第 7 条没有失效 (初始等级和结算等级一样) → 只显示一个数字
hist = [rec(1), rec(2, "failed"), rec(3), rec(4, "abandoned"), rec(5), rec(6),
        dict(rec(7), orig_level=7, level=7)]
page.contract_log = lambda: hist
page.fog = lambda: None
page._set_page("contracts")
root.update()
rows = page.contract_rows.winfo_children()
assert len(rows) == CONTRACT_ROWS, [type(r).__name__ for r in rows]
labels = []
for row in rows:
    labels += [w.cget("text") for w in row.winfo_children() if isinstance(w, tk.Label)]
assert "8-6" in labels and "7-5" in labels and "5-3" in labels and "3-1" in labels, labels
assert "7" in labels and "9-7" not in labels, labels      # 最新那条没失效: 只写 7
assert "等级" in labels

# 点一条 → 弹出词条: 降级的写降级后的名字, 失效的变灰
page._show_contract(9, 7, C.terms_of_record(hist[5]))
root.update()
dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
texts = []


def walk(w):
    for c in w.winfo_children():
        if isinstance(c, tk.Label):
            texts.append((c.cget("text"), str(c.cget("fg"))))
        walk(c)


walk(dlg)
names = [t for t, _ in texts]
assert "环境:持久战-III" in names and "环境:疲惫-II" in names and "环境:无言为真" in names, names
assert dict(texts)["环境:乐理之灾"] == config.COLORS["text_dim"]
assert dict(texts)["环境:疲惫-II"] == config.COLORS["text"]
dlg.destroy()

# 黑雾: 补满 5 条; 初始等级 -50 ~ 99, 失效等级 1 ~ 30; 词条随机
page.contract_log = lambda: hist[:2]          # 只有 2 条真实的
page.fog = lambda: 12345
page._contract_sig = None
page.refresh()
root.update()
rows = page.contract_rows.winfo_children()
assert len(rows) == CONTRACT_ROWS, len(rows)


def parse_level(text: str):
    m = re.match(r"^\(?(-?\d+)\)?$", text)
    if m:
        return int(m.group(1)), int(m.group(1))
    m = re.match(r"^\(?(-?\d+)\)?-\(?(-?\d+)\)?$", text)
    assert m, text
    return int(m.group(1)), int(m.group(2))


for row in rows:
    lv = [w.cget("text") for w in row.winfo_children()
          if isinstance(w, tk.Label) and re.match(r"^\(?-?\d+\)?(-\(?-?\d+\)?)?$", w.cget("text"))][0]
    a, b = parse_level(lv)
    assert -50 <= a <= 99 and 1 <= b <= 30, lv
# 同一个合约 (同一个种子) 里反复刷不变
before = [w.cget("text") for r in rows for w in r.winfo_children() if isinstance(w, tk.Label)]
page._contract_sig = None
page.refresh()
root.update()
after = [w.cget("text") for r in page.contract_rows.winfo_children() for w in r.winfo_children() if isinstance(w, tk.Label)]
assert before == after
# 假数据的词条也是随机的
data = page._contract_rows_data()
page._show_contract(data[0][0][0], data[0][0][1], data[0][1])
root.update()
dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
assert len(dlg.winfo_children()) > 0
dlg.destroy()

assert _level_text(-5, 3) == "-5-3" and _level_text(12, 9) == "12-9"
assert _level_text(7, 7) == "7" and _level_text(-5, -5) == "-5" and _level_text(-8, -3) == "-8--3"
page.fog = lambda: None
page.contract_log = lambda: []
page._contract_sig = None
page.refresh()
root.update()
assert len(page.contract_rows.winfo_children()) == 1        # "还没有顺利完成的合约."

root.destroy()
print("stats contracts check OK")
