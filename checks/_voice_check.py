"""语音协议、缓存、未播队列、并发清理和设置窗口自检，不联网、不播放声音。"""
import os
import sys
import json
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("voice_check")

import ai_voice
import config
from comm import Comm
from pages import voice_dialogs
from pages.widgets import Modal
from pages.comm_page import CommPage
from app.app_daily import DailyMixin


def wait_for(fn):
    end = time.monotonic() + 5
    while not fn() and time.monotonic() < end:
        time.sleep(.01)
    assert fn()


folder = tempstore.mkdtemp("voice")
calls = []
started, release = threading.Event(), threading.Event()


def generate(p, text):
    calls.append((p["voice_id"], text))
    if text == "delayed":
        started.set()
        assert release.wait(5)
    return b"ID3fake-mp3"


voice = ai_voice.Voice(folder, generate)
assert not voice.data["enabled"] and voice.data["gap"] == 1
assert voice.selected("Claudia.txt")["voice_id"] == "462298003509468"
assert voice.selected("Assistant.txt") is None
custom = ai_voice.profile("custom", "test")
custom.update(voice_id="test-voice", api_key="secret-key")
voice.save(enabled=True, profiles=voice.data["profiles"] + [custom], assign={"Assistant.txt": "claudia"})
assert voice.selected("Assistant.txt") is None, "Claudia 配置不能分配给别人"
voice.save(assign={"Assistant.txt": "custom"})
m = {"id": 1, "uid": "one", "text": "hello", "role": "ai"}
assert voice.request("Assistant.txt", m).result(3)
assert len(calls) == 1 and not voice.played("Assistant.txt", m)
assert isinstance(voice.request("Assistant.txt", m), str) and len(calls) == 1

# Key 存盘加密，同步使用绑定通道中的明文，到对方再重新加密。
with open(voice.path, encoding="utf-8") as f:
    assert "secret-key" not in f.read()
other = ai_voice.Voice(tempstore.mkdtemp("voice_other"), generate)
other.apply_doc(voice.sync_doc())
assert other.selected("Assistant.txt")["api_key"] == "secret-key"
reloaded = ai_voice.Voice(folder, generate)
assert reloaded.selected("Assistant.txt")["api_key"] == "secret-key"

# 只有播放器成功开始才记为已播放；再次播放不再调用接口。
commands = []
def mci(cmd):
    commands.append(cmd)
    return "stopped" if cmd.startswith("status") else ""
voice._mci = mci
voice.play("Assistant.txt", m)
wait_for(lambda: voice.play_state == "done")
assert voice.played("Assistant.txt", m)
voice.play("Assistant.txt", m)
wait_for(lambda: voice.play_state == "done")
assert len(calls) == 1

msgs = [{"id": i, "uid": str(i), "text": str(i), "role": "ai"} for i in range(30)]
assert [x["id"] for x in voice.autoplay("Assistant.txt", msgs)] == list(range(20, 30))
f = voice.file("Assistant.txt", msgs[-1])
with open(f + ".played", "w"):
    pass
assert [x["id"] for x in voice.autoplay("Assistant.txt", msgs)] == list(range(19, 29))

pending = {"id": 80, "uid": "delayed", "text": "delayed", "role": "ai"}
job = voice.request("Assistant.txt", pending)
assert voice.request("Assistant.txt", pending) is job
assert started.wait(3)
voice.clear("Assistant.txt")
release.set()
assert job.result(3) is None
assert not os.path.isdir(voice.folder("Assistant.txt"))

# 接口失败后可重试；失败不产生播放标记。
attempts = []
def retry(p, text):
    attempts.append(text)
    if len(attempts) == 1:
        raise OSError("secret-key")
    return b"ID3ok"
voice.generate = retry
assert voice.request("Assistant.txt", m).result(3) is None
assert "secret-key" not in voice.error
assert voice.request("Assistant.txt", m).result(3)

# 检查真实 MiniMax 请求体和 hex 响应解析，不联网。
class Response:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size): return json.dumps({"base_resp": {"status_code": 0}, "data": {"audio": b"ID3ok".hex()}}).encode()
requests = []
def open_request(req, timeout):
    requests.append(req)
    return Response()
with patch("urllib.request.build_opener", return_value=SimpleNamespace(open=open_request)):
    assert ai_voice.synthesize(custom, "hello") == b"ID3ok"
body = json.loads(requests[0].data)
assert body["voice_setting"]["voice_id"] == "test-voice" and body["output_format"] == "hex"
assert requests[0].get_header("Authorization") == "Bearer secret-key"

# 小屏大字号下语音配置字段仍可滚动到达，Claudia 音色不可修改。
root = tk.Tk()
root.geometry("800x600+5000+5000")
root.update()
c = Comm(tempstore.mkdtemp("voice_comm"), tempstore.mkdtemp("voice_personas"))
orig_wait = Modal.wait
def fake_wait(dlg):
    dlg.update_idletasks()
    def children(w):
        for child in w.winfo_children():
            yield child
            yield from children(child)
    all_ = list(children(dlg))
    assert any(w.get() == config.AI_VOICE_CLAUDIA_ID and str(w.cget("state")) == "readonly" for w in all_ if isinstance(w, ttk.Entry))
    assert any(str(w.cget("text")) == "帮助" for w in all_ if isinstance(w, ttk.Button))
    dlg.destroy()
Modal.wait = fake_wait
try:
    config.UI_SCALE = 1.5
    voice_dialogs.open_voice_settings(root, c)
finally:
    Modal.wait = orig_wait
    config.UI_SCALE = 1.0

# 收到消息就生成，清空聊天也清理音频；语音按钮和未播队列能定位较早消息。
c.voice.generate = generate
c.voice.save(enabled=True, auto=True)
hosts = DailyMixin._api_hosts(SimpleNamespace(comm=c))
assert "api.minimaxi.com" in hosts
c.voice.save(enabled=False)
assert "api.minimaxi.com" not in DailyMixin._api_hosts(SimpleNamespace(comm=c))
c.voice.save(enabled=True)
msg = c._mk("ai", "received")
c.messages.append(msg)
wait_for(lambda: os.path.isfile(c.voice.file(c.persona_name(), msg)))
page = CommPage(root, c)
page.pack(fill="both", expand=True)
page.show()
page._voice_scroll(msg)
assert msg["id"] in page._rendered

# 自动播放是按完整音频结束后计算间隔；手动点击取消排队，离开页面立即停止。
voice_calls = []
def play_fake(name, message):
    voice_calls.append(message["id"])
    c.voice.play_state = "playing"
c.voice.play = play_fake
messages = [dict(msg, id=i, uid=str(i), text=str(i)) for i in range(3)]
c.messages = messages
c._changed()
page.refresh(force=True)
page._voice_start()
with patch("pages.comm_voice.time.monotonic", return_value=100):
    page._voice_next = 100
    page._voice_tick()
    assert voice_calls == [0]
    page._voice_tick()
    assert voice_calls == [0], "不能在上一条还没播完时打断它"
    c.voice.play_state = "done"
    page._voice_tick()
    assert page._voice_next == 101
with patch("pages.comm_voice.time.monotonic", return_value=101):
    page._voice_tick()
    assert voice_calls == [0, 1]
page._voice_manual(messages[2])
assert not page._voice_queue and voice_calls == [0, 1, 2]
c.set_viewing(False)
assert c.voice.play_state == "idle"
c.clear()
assert not os.path.isdir(c.voice.folder(c.persona_name()))
page.hide()
root.destroy()
c.flush()
for item in (voice, other, reloaded, c.voice): item.close()
print("Voice check OK")
