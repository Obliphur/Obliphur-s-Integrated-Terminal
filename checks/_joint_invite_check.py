"""AI 发起联合行动 (MECHANICS 24.9.3): 合约页的邀请模式 + app 的 _joint_invite_watch (跳转 / 换词条 / 换回). 窗口开在屏幕外, 不碰网络."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("joint_invite_check")  # 临时文件放在 %TEMP%\OIT 里, 退出时整个删掉 (tempstore.py)

import tempfile
import tkinter as tk
import types

import config
config.UI_SCALE = 1.0
import contract as C
from pages.contract_page import ContractPage

root = tk.Tk()
root.geometry("1280x860+5000+5000")
root.update()
c = C.Contract(tempfile.mkdtemp())
inv = {}
answers = []
page = ContractPage(root, c, invite_fn=lambda: inv, on_invite=answers.append)
page.pack(fill="both", expand=True)
page.refresh()
root.update()

# 没有邀请: 和原来一样 (开始行动 + 清空, 没有 拒绝 / 接受)
assert page.btn_start.winfo_manager() == "pack" and page.inv_box.winfo_manager() != "pack"
assert page.btn_clear.winfo_manager() == "pack"

# 有邀请: 词条是对方选的 → 右下角只有 拒绝 / 接受, 词条板只能看, 清空也不行
c.set_draft(["retreat1", "deep1"])
inv.update({"id": "1", "hint": "🤝  Claudia 邀请你一起行动 14:00 - 18:48"})
page.refresh()
root.update()
assert page.inv_box.winfo_manager() == "pack" and page.btn_start.winfo_manager() != "pack" and page.btn_end.winfo_manager() != "pack"
assert page.btn_clear.winfo_manager() != "pack"
assert page.btn_reject.cget("text") == "拒绝" and page.btn_accept.cget("text") == "接受"
assert page.btn_reject.winfo_x() < page.btn_accept.winfo_x(), "拒绝在左, 接受在右"
assert "邀请你一起行动" in page.lbl_block.cget("text") and page.lbl_block.winfo_manager() == "pack"
assert "联合行动邀请" in page.lbl_state.cget("text")
before = list(c.draft)
page.page = 1
page._draw_tiles()
page._click("deep1")
page._click("short1")
page._clear()
assert c.draft == before, "邀请中不能改词条: " + str(c.draft)
page.btn_reject.event_generate("<Button-1>")
page.btn_accept.event_generate("<Button-1>")
root.update()
assert answers == [False, True], answers

# 邀请结束 (回复了 / 错过 / 聊天被清空): 回到原来的开始行动
inv.clear()
page.refresh()
root.update()
assert page.inv_box.winfo_manager() != "pack" and page.btn_start.winfo_manager() == "pack" and page.btn_clear.winfo_manager() == "pack"
assert page.btn_start.cget("text") == "开始行动" and page.lbl_state.cget("text") == ""
page._click("deep1")
assert "deep1" not in c.draft, "邀请结束后词条又能改了"
root.destroy()

# ---- app._joint_invite_watch (用假的 self 调用, 不启动程序) ----
import app as A
from app_parts import app_comm
from datetime import datetime

log = []
app_comm.toast = types.SimpleNamespace(notice=lambda *a, **k: log.append(("notice", a)), info=lambda *a, **k: log.append(("info", a)),
                                      warn=lambda *a, **k: log.append(("warn", a)))


class FakeComm:
    def __init__(self):
        self.invite = {}
        self.state = ""
        self.blocked = ""
        self.answer = ""

    def joint_invite(self):
        return dict(self.invite)

    def joint_state(self):
        return self.state

    def joint_partner(self):
        return "Claudia"

    def joint_respond(self, accept):
        self.state = "yes" if accept else "no"
        self.invite = {}
        return self.answer


def make(draft):
    me = types.SimpleNamespace()
    me.comm = FakeComm()
    me.contract = types.SimpleNamespace(active=False, draft=list(draft))
    me.contract.set_draft = lambda terms: setattr(me.contract, "draft", list(terms))
    me.shutting_down = False
    me.current_page = "timer"
    me._inv_id = ""
    me._inv_prev = []
    me.shown = []
    me._comm_blocked = lambda: me.comm.blocked
    me._joint_pick_cancel = lambda: me.shown.append("pick_cancel")
    me._paint_sidebar = lambda: None
    me.show_page = lambda key: (me.shown.append(key), setattr(me, "current_page", key))
    me._refresh_if_visible = lambda *k: me.shown.append("refresh")
    watch = lambda: A.BreakTimerApp._joint_invite_watch(me)
    me._joint_invite_watch = watch
    respond = lambda ok: A.BreakTimerApp._joint_invite_respond(me, ok)
    return me, watch, respond


INV = {"id": "100", "terms": ["retreat1", "deep1"], "start": "2026-11-04T14:00:00", "end": "2026-11-04T18:48:00"}

# 1. 邀请到了: 跳到合约页, 词条换成对方选的, 用户原来选的记着
me, watch, respond = make(["short1"])
watch()
assert me.shown == [] and me.contract.draft == ["short1"], "没有邀请什么也不做"
me.comm.invite = dict(INV)
watch()
assert "contract" in me.shown and me.contract.draft == INV["terms"] and me._inv_id == "100" and me._inv_prev == ["short1"]
assert any(k == "notice" for k, _ in log), "有一条提示"
n = len(me.shown)
watch()
assert len(me.shown) == n, "同一个邀请只跳一次 (用户之后去了别的页面也不再拉回来)"

# 2. 拒绝: 换回原来的词条
respond(False)
assert me.contract.draft == ["short1"] and me._inv_id == "" and me._inv_prev == [], me.contract.draft

# 3. 接受: 词条留着 (到点在合约页开始)
me, watch, respond = make(["short1"])
me.comm.invite = dict(INV)
watch()
respond(True)
assert me.contract.draft == INV["terms"] and me._inv_id == ""

# 4. 现在不方便 (沟通不能用 / 合约中 / 指令页): 不跳, 邀请还在; 能跳了再跳
me, watch, respond = make([])
me.comm.invite = dict(INV)
me.comm.blocked = "专注中不能使用沟通"
watch()
assert me.shown == [] and me._inv_id == "" and me.contract.draft == []
me.comm.blocked = ""
me.current_page = "prescript"
watch()
assert me.shown == [] and me._inv_id == ""
me.current_page = "timer"
me.contract.active = True
watch()
assert me.shown == [] and me._inv_id == ""
me.contract.active = False
watch()
assert "contract" in me.shown and me._inv_id == "100" and me.contract.draft == INV["terms"] and me._inv_prev == []

# 5. 邀请过期 / 聊天被清空 (邀请没了, 也没接受): 换回原来的
me, watch, respond = make(["short1"])
me.comm.invite = dict(INV)
watch()
me.comm.invite = {}
me.comm.state = "missed"
watch()
assert me.contract.draft == ["short1"] and me._inv_id == ""

# 6. 重启后还有没回复的邀请, 词条本来就是对方选的: 拒绝以后不要把对方的词条当成"原来选的"留下
me, watch, respond = make(INV["terms"])
me.comm.invite = dict(INV)
watch()
assert me._inv_prev == []
respond(False)
assert me.contract.draft == []

# 7. 新邀请直接顶掉旧的 (旧的还没回复): 原来选的词条不能被旧邀请的词条覆盖
me, watch, respond = make(["short1"])
me.comm.invite = dict(INV)
watch()
me.comm.invite = dict(INV, id="101", terms=["deep1"])
watch()
assert me._inv_id == "101" and me.contract.draft == ["deep1"] and me._inv_prev == ["short1"]

# 8. 点了 接受 / 拒绝 却被拒绝 (开始时间过了等): 弹提示
me, watch, respond = make(["short1"])
me.comm.invite = dict(INV)
watch()
me.comm.answer = "约定的开始时间已经过了"
log.clear()
respond(True)
assert any(k == "warn" for k, _ in log)
print("joint invite check OK")
