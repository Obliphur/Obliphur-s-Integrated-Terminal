import os
import re
import shutil
import tempfile
import tkinter as tk

# 用临时目录当用户目录: 检查不会碰真实的数据 (违规记录要写文件)
_TMP = tempfile.mkdtemp(prefix="oit_stats_check_")
os.environ["USERPROFILE"] = _TMP
os.environ["APPDATA"] = os.path.join(_TMP, "AppData", "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(_TMP, "AppData", "Local")

import config
config.UI_SCALE = 1.0
import contract as C
from data_manager import DataManager, VIOLATION_KEEP
from pages.stats_page import StatsPage, CONTRACT_ROWS, VIOLATION_ROWS, _level_text

root = tk.Tk()
root.withdraw()
dm = DataManager()


class _Net:
    def usage_by_reason(self, prefix):
        return {}


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

# ---------- 合约页: 通过记录 / 违规记录 两个模式 ----------

assert "violations" not in page.tab_btns, "违规记录不再是单独一栏"
assert "contracts" in page.tab_btns and set(page.contract_btns) == {"done", "violations"}
assert VIOLATION_ROWS == VIOLATION_KEEP == 15

# 存: 只留最近 15 条, 新的在前, 换一个 DataManager 也能读回来
for i in range(20):
    dm.add_violation(f"⚠ 违反合约: 第 {i} 次 · 目标生命值 1 / 4\n违反一次  ·  危机等级 3")
assert len(dm.violations) == 15, len(dm.violations)
assert "第 19 次" in dm.recent_violations()[0]["text"]
assert "第 5 次" in dm.recent_violations()[-1]["text"]
dm2 = DataManager()
assert len(dm2.violations) == 15 and dm2.recent_violations()[0]["text"] == dm.recent_violations()[0]["text"]

page.violation_log = lambda: dm.recent_violations()
page.contract_log = lambda: hist
page.fog = lambda: None
page._violation_sig = None
page._contract_sig = None

# 通过记录在左, 违规记录在右
order = [w.cget("text") for w in page.contract_mode_bar.pack_slaves()]
assert order == ["通过记录", "违规记录"], order

page._set_page("contracts")                      # 默认: 通过记录
root.update()
assert page.contract_mode == "done"
assert page.contracts_body.winfo_manager() == "pack" and page.violations_body.winfo_manager() == ""
assert len(page.contract_rows.winfo_children()) == CONTRACT_ROWS

page._set_contract_mode("violations")            # 切到违规记录
root.update()
assert page.violations_body.winfo_manager() == "pack" and page.contracts_body.winfo_manager() == ""
rows = page.violation_rows.winfo_children()
assert len(rows) == VIOLATION_ROWS, len(rows)
texts0 = [w.cget("text") for w in rows[0].winfo_children() if isinstance(w, tk.Label)]
assert any("第 19 次" in t for t in texts0) and any("目标生命值" in t for t in texts0), texts0
assert texts0[0].count(":") == 1, texts0          # 上面一行是时间

# 两个板块都不再有小字描述
small = []
for body in (page.contracts_body, page.violations_body):
    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, tk.Label):
                small.append(c.cget("text"))
            walk(c)
    walk(body)
assert not any("最近 5 条" in t or "最近 15 条" in t for t in small), small

# 黑雾: 全部换成 ???, 不足 15 条补满
page.fog = lambda: 12345
page._violation_sig = None
page.refresh()
root.update()
rows = page.violation_rows.winfo_children()
assert len(rows) == VIOLATION_ROWS, len(rows)
for r in rows:
    ts = [w.cget("text") for w in r.winfo_children() if isinstance(w, tk.Label)]
    assert ts == ["???", "???"], ts

# 只有 3 条真记录时黑雾照样补满 15 条 ???
page.fog = lambda: None
page.violation_log = lambda: dm.recent_violations()[:3]
page._violation_sig = None
page.refresh()
root.update()
assert len(page.violation_rows.winfo_children()) == 3
page.fog = lambda: 7
page._violation_sig = None
page.refresh()
root.update()
assert len(page.violation_rows.winfo_children()) == 15

# 空
page.fog = lambda: None
page.violation_log = lambda: []
page._violation_sig = None
page.refresh()
root.update()
assert len(page.violation_rows.winfo_children()) == 1        # "还没有违规记录."

# 切回通过记录
page._set_contract_mode("done")
root.update()
assert page.contracts_body.winfo_manager() == "pack" and page.violations_body.winfo_manager() == ""

# 再打开一次合约页: 又回到通过记录 (上次停在违规记录也一样)
page._set_contract_mode("violations")
root.update()
page._set_page("net")
root.update()
page._set_page("contracts")
root.update()
assert page.contract_mode == "done"
assert page.contracts_body.winfo_manager() == "pack" and page.violations_body.winfo_manager() == ""

root.destroy()
shutil.rmtree(_TMP, ignore_errors=True)
print("stats check OK (contracts: 通过记录 / 违规记录)")
