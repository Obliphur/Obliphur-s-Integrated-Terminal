"""沟通模式入口：未解锁也可见可点，窄窗口 / 大字号不挤掉，帮助说明可滚动。"""
import os
import sys
import tkinter as tk
import zlib
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("comm_mode_check")

import config
import i18n
from comm import Comm
from pages.comm_page import CommPage
from pages.settings_page import SettingsPage
from settings_manager import SettingsManager
from pages.widgets import Modal, ScrollArea


def _valid_png(w=8, h=8, rgb=(40, 120, 220)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(tag, data):
        return len(data).to_bytes(4, "big") + tag + data + zlib.crc32(tag + data).to_bytes(4, "big")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0]))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


root = tk.Tk()
root.geometry("980x680+5000+5000")
folder = tempstore.mkdtemp("comm_mode")
c = Comm(os.path.join(folder, "data"), os.path.join(folder, "personas"))
assert c.answer_images_on(), "解答图片模式默认开启"
original_scale = config.UI_SCALE
original_language = i18n.get_language()
original_show_tension = config.COMM_SHOW_TENSION
sm = SettingsManager(os.path.join(folder, "settings.json"))
settings = SettingsPage(root, sm, data_dir=folder)
settings.show_tab("net")
# 真正通过设置页提交：口令不修改联网冷静期、不影响开发者模式，并恢复填写框。
row = settings._num_rows["COOLDOWN"]
before = (dict(sm.effective), dict(sm.pending), config.dev_mode_enabled())
for expected in (True, False):
    row["var"].set("325799")
    assert settings._stage_num("COOLDOWN") == ("tension", "")
    assert config.COMM_SHOW_TENSION is expected
    assert row["var"].get() == sm.ui_number("COOLDOWN")
    assert (dict(sm.effective), dict(sm.pending), config.dev_mode_enabled()) == before
settings.destroy()
split_settings = SettingsPage(root, sm, data_dir=folder, comm=c)
split_settings.show_tab("comm")
root.update()
assert not c.split_on() and c.split_interval() == 0
assert not any("分条发送" in str(w.cget("text")) for w in descendants(split_settings) if "text" in w.keys())
c.apply_doc({"split": False, "split_interval": 8})
assert not c.split_on() and c.split_interval() == 0
split_settings.destroy()
try:
    for language in ("zh", "en"):
        i18n.set_language(language)
        for scale in (1.0, 1.5):
            config.UI_SCALE = scale
            page = CommPage(root, c)
            page.pack(fill="both", expand=True)
            # 图片附件按钮始终是 📎；待发区展示缩略图和独立取消按钮，剪贴板位图写入临时文件，并监听 <<Paste>>。
            assert page.entry.bind("<<Paste>>")
            image_file = os.path.join(folder, "paste.png")
            png_bytes = _valid_png()
            with open(image_file, "wb") as stream:
                stream.write(png_bytes)
            assert page._set_pending_image(image_file)
            assert page.btn_image.cget("text") == "📎"
            assert page.lbl_pending_image.cget("image")
            assert page.btn_pending_image_x.winfo_manager() == "pack"
            assert page.btn_joint_x.winfo_manager() != "pack"
            def copy_bitmap(target):
                with open(target, "wb") as stream:
                    stream.write(png_bytes)
                return target
            with patch("pages.comm_page.avatar_mod.clipboard_paths", return_value=[]), \
                    patch("pages.comm_page.avatar_mod.clipboard_image", side_effect=copy_bitmap) as copy_image:
                assert page._paste_image() == "break"
                assert os.path.isfile(copy_image.call_args.args[0])
                assert page.btn_image.cget("text") == "📎"
                assert page.lbl_pending_image.cget("image")
                assert page.btn_pending_image_x.winfo_manager() == "pack"
                assert page.btn_joint_x.winfo_manager() != "pack"
            page._clear_package_drafts()
            assert page._pending_image == ""
            assert page.btn_pending_image_x.winfo_manager() != "pack"
            assert page.btn_joint_x.winfo_manager() != "pack"
            # 发出的图片消息应保存本地文件、在气泡中渲染缩略图且点击可打开大图弹窗。
            assert page._set_pending_image(image_file)
            with patch.object(c, "problem", return_value=""), patch.object(c, "_ask_user"):
                page._send()
            assert page._pending_image == ""
            saved_img = c.chat_image_path(c.messages[-1]["img"])
            assert os.path.isfile(saved_img)
            root.update()
            thumb_labels = [w for w in descendants(page.area.inner) if isinstance(w, tk.Label) and str(w.cget("image"))]
            assert thumb_labels, "发出的图片消息应渲染气泡缩略图"
            opened_imgs = []
            def inspect_viewer(dlg):
                imgs = [w for w in descendants(dlg.body) if isinstance(w, tk.Label) and str(w.cget("image"))]
                opened_imgs.append(len(imgs))
                dlg.cancel()
            with patch.object(Modal, "wait", inspect_viewer):
                page._show_image(saved_img)
            assert opened_imgs == [1], "点击气泡图片应打开预览大图弹窗"
            c.messages.clear()
            page.refresh(force=True)
            # 阅读入口的两个选择和关闭行为, 中英文均走真实按钮回调。
            assert page.btn_coread.cget("text") == "📚"
            opened = page.on_coread = Mock()
            with patch.object(c, "toggle_request_draft", return_value="") as stage:
                for label, value in (("自己阅读", False), ("发起 coread", True)):
                    def choose(dlg, label=label, value=value):
                        button = next(w for w in descendants(dlg) if "text" in w.keys() and w.cget("text") == i18n.tr(label))
                        button.invoke()
                        return dlg.result
                    with patch.object(Modal, "wait", choose):
                        page.btn_coread.invoke()
                    if value:
                        stage.assert_called_with("coread")
                    else:
                        opened.assert_called_with(together=False)
                assert opened.call_count == 1
            with patch.object(Modal, "wait", lambda dlg: dlg.cancel()):
                page.btn_coread.invoke()
            assert opened.call_count == 1
            c.cfg["msg_gone"] = 199
            c.cfg["mem_fixed"] = []
            c._interaction_set({"scenario_mode": False})
            # 仅开关开启时显示真实张力；不受情景模式、消息门槛和90档位封顶影响。
            page._sync_status()
            assert not page.lbl_tension.winfo_manager()
            assert sm.stage("COOLDOWN", sm.from_ui_number("COOLDOWN", "325799"))[0] == "tension"
            c._interaction_set({"tension": 95.126})
            with patch.object(c, "other_status_display", return_value=("", "idle")):
                page._sync_status()
                root.update()
            assert page.lbl_tension.winfo_ismapped() and page.lbl_tension.cget("text") == "Tension: 95.13 | Valence: 0"
            c._interaction_set({"tension": 42.5})
            page._sync_status()
            assert page.lbl_tension.cget("text") == "Tension: 42.5 | Valence: 0"
            assert sm.stage("COOLDOWN", sm.from_ui_number("COOLDOWN", "325799"))[0] == "tension"
            page._sync_status()
            assert not page.lbl_tension.winfo_manager() and page.lbl_tension.cget("text") == ""
            c._interaction_set({"scenario_mode": False})
            # 很长的对方状态不能挤掉模式入口。
            with patch.object(c, "other_status_display", return_value=("long status " * 80, "idle")):
                page.refresh(force=True)
                root.update()
            for button in (page.btn_scenario,):
                assert button.winfo_ismapped()
                assert not button.instate(["disabled"])
                assert button.winfo_rootx() >= page.winfo_rootx()
                assert button.winfo_rootx() + button.winfo_width() <= page.winfo_rootx() + page.winfo_width()
                assert button.master == page.btn_mute.master
                assert button.winfo_rooty() >= page.area.winfo_rooty() + page.area.winfo_height()
                assert button.winfo_rooty() + button.winfo_height() <= page.entry.winfo_rooty()
            assert page.btn_scenario.cget("text") == "📱"
            with patch("pages.comm_page.toast.info") as hint:
                page.btn_scenario.invoke()
                assert not c.scenario_on()
                hint.assert_not_called()

            c.cfg["msg_gone"] = 200
            page.refresh(force=True)
            page.btn_scenario.invoke()
            assert c.scenario_on()
            assert page.btn_scenario.cget("text") == "🎭"
            page.btn_scenario.invoke()
            assert not c.scenario_on()
            assert page.btn_scenario.cget("text") == "📱"

            def inspect_help(dlg):
                dlg.update_idletasks()
                text = "\n".join(str(w.cget("text")) for w in descendants(dlg) if "text" in w.keys())
                assert i18n.tr("聊天模式") in text and "📱 / 🎭" in text
                assert i18n.tr("说教") in text
                assert i18n.tr("阅读") in text and "coread" in text
                assert i18n.tr("你可以请求对方针对现实中想改变的状态、行为、习惯或语言进行说教，分条分析问题与解决方案；主题会简短记入记忆卡，实际完成改变后才移除。") in text
                assert "200" not in text
                area = next(w for w in descendants(dlg) if isinstance(w, ScrollArea))
                assert area.winfo_height() > 0
                assert dlg.winfo_height() <= dlg.winfo_screenheight() * .85 + 1
                area.canvas.yview_moveto(1)
                dlg.update_idletasks()
                assert area.canvas.yview()[1] >= .99
                dlg.destroy()

            with patch.object(Modal, "wait", inspect_help):
                page._help()
            # 未到显示时间没有半条气泡，到点后一次显示完整正文。
            from datetime import datetime
            original_messages = c.messages
            message = {"id": 1, "uid": "delayed-ui", "role": "ai", "text": "中a完整回复", "hold": True,
                       "due": 300, "ts": 1, "at": "2026-11-18T12:00:00"}
            c.messages = [message, {"id": 2, "role": "me", "text": "继续", "ts": 2, "at": message["at"]}]
            try:
                with patch.object(c, "blocked", lambda: ""), patch.object(c, "_away_kind", lambda: ""), \
                        patch.object(c, "_ms", lambda: 299), patch.object(c, "now", lambda: datetime(2026, 11, 18, 12)):
                    page.refresh(force=True)
                    c._release_held()
                    assert page._rendered == [2] and message["hold"]
                with patch.object(c, "blocked", lambda: ""), patch.object(c, "_away_kind", lambda: ""), patch.object(c, "_ms", lambda: 300):
                    c._release_held()
                    page._sync_rows()
                    assert page._rendered == [1, 2] and not message.get("hold")
                    assert any(w.cget("text") == "中a完整回复" for w in descendants(page._rows[0]) if "text" in w.keys())
            finally:
                c.messages = original_messages
            if page._resize_job:
                page.after_cancel(page._resize_job)
            page.destroy()
    # 服务启动必须在 Tk 主线程反馈结果, 独自阅读不要求先选择人设。
    from app.app_comm import CommMixin
    class ReadingApp(CommMixin):
        pass
    reading_app = ReadingApp()
    reading_app.root = root
    reading_app.dm = SimpleNamespace(data_dir=folder)
    reading_app.comm = SimpleNamespace(pid="", set_cfg=Mock())
    reading_app._log_window = Mock()
    for states, error, together in ((["stopped", "ready"], "", False),
                                    (["stopped", "error"], "test startup failure", True)):
        service = Mock(access_key="test-key", progress="preparing", error=error)
        service.refresh.side_effect = states
        service.lan_hosts.return_value = ["192.0.2.1"]
        service.url.side_effect = lambda host="127.0.0.1": f"http://{host}:3000/?access_key=test-key"
        reading_app.coread_service = service
        reading_app.comm.pid = "character" if together else ""
        with patch("coread_service.OitChatBridge"), patch("webbrowser.open", return_value=True) as browser, \
                patch("app.app_comm.toast.warn") as warning:
            reading_app._open_coread(together=together)
            if error:
                browser.assert_not_called()
                assert error in warning.call_args.args[0]
            else:
                assert browser.call_args.args[0].startswith("http://127.0.0.1:")
                assert "reading_mode=solo" in browser.call_args.args[0]
                assert "192.0.2.1" in reading_app.comm.set_cfg.call_args.kwargs["coread_link"]["url"]
                warning.assert_not_called()
finally:
    config.COMM_SHOW_TENSION = original_show_tension
    config.UI_SCALE = original_scale
    i18n.set_language(original_language)
    c.flush()
    c.voice.close()
    root.destroy()

print("Comm mode check OK")
