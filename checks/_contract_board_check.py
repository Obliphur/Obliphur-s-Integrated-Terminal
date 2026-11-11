import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("contract_board_check")     # 临时文件放在 %TEMP%\OIT 里, 退出时整个删掉 (tempstore.py)

import tempfile
import tkinter as tk

import config
config.UI_SCALE = 1.0
import contract as C
from pages.contract_page import ContractPage

root = tk.Tk()
root.geometry("1280x860+5000+5000")
root.update()
c = C.Contract(tempfile.mkdtemp())
page = ContractPage(root, c)
page.pack(fill="both", expand=True)
page.refresh()
root.update()

# 翻页后的分页: 1280 宽应该正好两页词条 + 一页组合
assert page.pages == C.PAGES, page.pages
assert page._page_count() == len(C.PAGES) + 1

# 每个词条都在自己那一页上画出来了, 一共 len(TERMS) 个
total = 0
for i in range(len(page.pages)):
    page.page = i
    page._draw_tiles()
    root.update_idletasks()
    a, b = page.pages[i]
    want = sum(1 for t in C.TERMS if a <= C.LAYOUT[t[0]][0] < b)
    got = len([x for x in page.canvas.find_withtag("tile") if page.canvas.type(x) == "polygon"])
    assert got == want, (i, got, want)
    total += got
assert total == len(C.TERMS), (total, len(C.TERMS))
# 第 2 页 10 列 (右边的零散词条)
assert page.pages[1] == (C.PAGE_SPLIT, C.PAGE_SPLIT + 10)

# 新高机动 / 禁足 能点, 点了进草稿, 再点取消 (两个都要先选中"目标"的前置: gaze)
page.page = 1
page._draw_tiles()
root.update_idletasks()
assert C.BY_ID["move"][3] == "目标:高机动" and C.BY_ID["confine"][3] == "目标:禁足"
page._click("gaze")
assert "gaze" in c.draft, c.draft
page._click("move")
assert "move" in c.draft, c.draft
page._click("confine")
assert "confine" in c.draft, c.draft
page._click("move")
assert "move" not in c.draft and "confine" in c.draft
# 记录封锁的新描述
assert C.BY_ID["seal"][4] == "无法修改已经被填写的格子 (包括自动填写和手动填写); 每次完成填写时, 另外随机封锁两个可以封锁的格子"

# 第 3 页: 组合效果. 要凑的词条多了以后, "需要" 那行不能压到下面的效果文字
import i18n


def check_combos(label):
    page.page = page._page_count() - 1
    page._draw_tiles()
    root.update_idletasks()
    for combo in C.COMBOS:
        items = page.canvas.find_withtag(f"c_{combo['id']}")
        texts = [i for i in items if page.canvas.type(i) == "text"]
        # 名字 / 状态 / 需要 三行 (效果文字已经并进词条描述, 不再单独一行)
        assert len(texts) == 3, (combo["name"], len(texts))
        card = next(i for i in items if page.canvas.type(i) == "rectangle")
        need = page.canvas.bbox(texts[2])
        assert need[3] <= page.canvas.coords(card)[3], (label, combo["name"], need)


check_combos("zh")
i18n.set_language("en")
check_combos("en")
i18n.set_language("zh")

root.destroy()
print("contract board check OK")
