import os
import tempfile
import tkinter as tk

import config
config.UI_SCALE = 1.0
import settings_manager as SM
from pages.settings_page import SettingsPage

root = tk.Tk()
root.withdraw()
sm = SM.SettingsManager(os.path.join(tempfile.mkdtemp(), "settings.json"))
page = SettingsPage(root, sm, data_dir=tempfile.mkdtemp())
page.pack(fill="both", expand=True)
root.update_idletasks()

# 新的一页: 设置 → 日常
assert "daily" in page.areas, "应该多一页日常"
assert "daily" in page._tab_btns and page._tab_btns["daily"].cget("text") == "日常"

page.show_tab("daily")
page.refresh()
root.update_idletasks()
# 行为模式 (MECHANICS 21) 在最上面, 两个自动记录文本也是选项
assert "BEHAVIOR_MODE" in page._rows, "日常页应该有行为模式"
assert page._rows["BEHAVIOR_MODE"]["var"].get() == "自由学习"
assert "OUTING_TEXT" in page._rows and "REST_TEXT" in page._rows
assert page._rows["OUTING_TEXT"]["var"].get() == "外出"
assert page._rows["REST_TEXT"]["var"].get() == "休息"
# 文本项: 提交后排队, 填写框回到当前值
page._rows["OUTING_TEXT"]["var"].set("上班")
page._stage("OUTING_TEXT")
assert sm.is_staged("OUTING_TEXT")
assert page._rows["OUTING_TEXT"]["var"].get() == "外出"
sm.cancel("OUTING_TEXT")
assert any(isinstance(w, tk.Label) and w.cget("text") == "🌙  睡觉时间"
           for w, _k, _t in page._i18n_widgets), "睡眠时间要有标题"
keys = [f"SLEEP_{d}" for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")]
for k in keys:
    assert k in page._num_rows, f"{k} 应该有填写框"
    assert page._num_rows[k]["var"].get() == "23:00", f"{k} 默认 23:00"
    assert "23:00" in page._num_rows[k]["lbl_current"].cget("text")
    assert page._num_rows[k]["name"].cget("text") in ("周一", "周二", "周三", "周四",
                                                       "周五", "周六", "周日")

# 填 HH:MM: 提交后进入冷静期排队, 当前值还是 23:00
row = page._num_rows["SLEEP_MON"]
row["var"].set("22:30")
status, _ = page._stage_num("SLEEP_MON", quiet=True)
assert status == "staged"
assert sm.pending["SLEEP_MON"]["value"] == 1350
assert row["var"].get() == "23:00", "填回当前生效值"
assert row["pend"].winfo_manager() == "grid", "待生效的改动有单独一行"
assert "22:30" in row["lbl_pending"].cget("text")

# 填错: 拒绝, 排队不变
sm.cancel("SLEEP_MON")
row["var"].set("22:99")
assert page._stage_num("SLEEP_MON", quiet=True)[0] == "invalid"
assert "SLEEP_MON" not in sm.pending

# 原来联网页的星期倍率没被分组的改动弄坏
page.show_tab("net")
page.refresh()
root.update_idletasks()
for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"):
    assert f"QUOTA_MULT_{d}" in page._num_rows
    assert page._num_rows[f"QUOTA_MULT_{d}"]["var"].get() == "1"

root.destroy()
print("daily settings page check OK")
