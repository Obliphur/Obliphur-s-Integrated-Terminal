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
import ai_voice_design
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
assert voice.selected("Claudia.txt")["voice_id"] == "claudia1999"
voice.data["profiles"][0]["voice_id"] = "462298003509468"
voice._normalize()
assert voice.selected("Claudia.txt")["voice_id"] == "claudia1999"
voice.data["profiles"][0]["voice_id"] = "custom-claudia"
voice._normalize()
assert voice.selected("Claudia.txt")["voice_id"] == "custom-claudia"
assert voice.selected("Assistant.txt") is None
custom = ai_voice.profile("custom", "test")
custom.update(voice_id="test-voice", api_key="secret-key")
voice.save(enabled=True, profiles=voice.data["profiles"] + [custom], assign={"Assistant.txt": "claudia"})
assert voice.selected("Assistant.txt") is None, "Claudia 配置不能分配给别人"
voice.save(assign={"Assistant.txt": "custom"})
m = {"id": 1, "uid": "one", "text": "hello", "role": "ai"}
assert voice.key("Claudia.txt", dict(m, uid="fixture")) == "2a64e3c54fe4ad9286a9aa72bba143f543ded230cf431ced3fc78a1ab3b6ccc1"
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

# 两台真实 HTTP 同步服务：自动补传缓存、合并已听状态，不重复生成或通知文件送达。
import devsync

class VoiceLocal(devsync._FakeLocal):
    def __init__(self, name):
        super().__init__(name)
        self.comm = Comm(tempstore.mkdtemp("voice_sync"), tempstore.mkdtemp("voice_personas"))
        self.generated = 0
        self.delivered = []
        def generate_audio(p, text):
            self.generated += 1
            return b"ID3" + text.encode("utf-8") * 100
        self.comm.voice.generate = generate_audio

    def docs(self):
        return {"comm": self.comm.sync_doc()}

    def apply(self, doc, changes):
        self.comm.apply_doc(changes)

    def voice_files(self):
        return self.comm.voice_files()

    def voice_played(self, keys):
        self.comm.voice_played(keys)

    def voice_received(self, key, source):
        self.comm.voice_received(key, source)

    def file_delivered(self, *args):
        self.delivered.append(args)

la, lb = VoiceLocal("A"), VoiceLocal("B")
runner = devsync.MainRunner()
pump_stop = threading.Event()
def pump_sync():
    while not pump_stop.wait(.005):
        runner.pump()
pump_thread = threading.Thread(target=pump_sync, daemon=True)
pump_thread.start()
sa = devsync.SyncService(tempstore.mkdtemp("voice_service"), la, runner, http_port=18320, udp_port=0)
sb = devsync.SyncService(tempstore.mkdtemp("voice_service"), lb, runner, http_port=18340, udp_port=0)
try:
    sa.start(); sb.start()
    key = os.urandom(32).hex()
    for left, right in ((sa, sb), (sb, sa)):
        left.state["peers"][right.my_id] = {"key": key, "addr": "127.0.0.1", "port": right.port, "name": "peer"}
    la.comm.voice.save(enabled=True)
    sm = la.comm._mk("ai", "你好，同步语音")
    la.comm.messages.append(sm)
    wait_for(lambda: os.path.isfile(la.comm.voice.file("Claudia.txt", sm)))
    assert sa._sync_with(sb.my_id), sa.peers()
    target = lb.comm.voice.file("Claudia.txt", sm)
    wait_for(lambda: os.path.isfile(target))
    with open(target, "rb") as f, open(la.comm.voice.file("Claudia.txt", sm), "rb") as source:
        assert f.read() == source.read()
    assert lb.generated == 0, "同步消息必须复用原设备音频"
    lb.comm.voice.mark_played(target)
    assert sa._sync_with(sb.my_id), sa.peers()
    assert la.comm.voice.played("Claudia.txt", sm), "子端听过的消息要回传母端"
    assert not la.comm.voice.autoplay("Claudia.txt", [sm])
    assert not la.delivered and not lb.delivered, "自动语音传输不能弹文件送达通知"
    # 子端也能把缓存补回母端，不受谁主动发同步请求影响。
    os.remove(la.comm.voice.file("Claudia.txt", sm))
    assert sa._sync_with(sb.my_id), sa.peers()
    wait_for(lambda: os.path.isfile(la.comm.voice.file("Claudia.txt", sm)))
    # 远端旧的未听状态不能把本机已听状态改回去。
    old = sa.snapshot()
    for entry in old["voices"].values(): entry["played"] = False
    sb.merge(old)
    assert lb.comm.voice.played("Claudia.txt", sm)
    # 下载已校验、尚未落盘时清空聊天：迟到结果必须被丢弃。
    lb.comm.voice.clear("Claudia.txt")
    wait_for(lambda: not getattr(sa, "_voice_sending", set()))
    downloaded, finish_download = threading.Event(), threading.Event()
    fetch = sb.fetch_file
    def delayed_fetch(*args):
        fetch(*args)
        downloaded.set()
        assert finish_download.wait(5)
    sb.fetch_file = delayed_fetch
    sa._send_voices(sb.my_id, sb.snapshot())
    assert downloaded.wait(5)
    lb.comm.clear()
    finish_download.set()
    wait_for(lambda: not getattr(sb, "_voice_receiving", set()))
    assert not os.path.isdir(lb.comm.voice.folder("Claudia.txt"))
    sb.merge(old)
    assert not os.path.isdir(lb.comm.voice.folder("Claudia.txt")), "旧快照不能恢复清空后的播放记录"
finally:
    sa.stop(); sb.stop()
    pump_stop.set(); pump_thread.join(1)
    la.comm.voice.close(); lb.comm.voice.close()

# 播放门禁独立于聊天可用性；非空闲 / 休息不调用播放器，生成仍可后台进行。
play_state = {"kind": "idle"}
gate_started, gate_release = threading.Event(), threading.Event()
def gate_generate(p, words):
    if words == "wait-for-state":
        gate_started.set()
        assert gate_release.wait(5)
    return b"ID3gate"
gate = ai_voice.Voice(tempstore.mkdtemp("misc", prefix="voice_gate_"), gate_generate)
gate.data.update(enabled=True, profiles=[custom], assign={"Assistant.txt": "custom"})
gate.play_allowed = lambda: play_state["kind"] in ("idle", "break")
gate_commands = []
def gate_mci(cmd):
    gate_commands.append(cmd)
    return "playing" if cmd.startswith("status") else ""
gate._mci = gate_mci
gm = dict(m, uid="gate", text="state-restricted")
for kind in ("focus", "sleep", "out", ""):
    play_state["kind"] = kind
    gate.play("Assistant.txt", gm)
    assert not any(cmd.startswith("play ") for cmd in gate_commands)
    assert not gate.played("Assistant.txt", gm)
assert gate.request("Assistant.txt", gm).result(3), "限制播放不限制新消息生成"
play_state["kind"] = "break"
gate.play("Assistant.txt", gm)
wait_for(lambda: gate.play_state == "playing")
play_state["kind"] = "focus"
wait_for(lambda: gate.play_state == "idle")
play_state["kind"] = "idle"
waiting = dict(gm, uid="gate-wait", text="wait-for-state")
gate.play("Assistant.txt", waiting)
assert gate_started.wait(3)
play_state["kind"] = "sleep"
gate_release.set()
wait_for(lambda: gate.play_state == "idle")
assert not gate.played("Assistant.txt", waiting), "加载期间切状态不能在生成完成后偷播"
gate.close()

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

# 同步先落盘时，尚未结束的本机生成不能覆盖传来的音频。
voice.generate = generate
started.clear(); release.clear()
race = {"id": 81, "uid": "receive-race", "text": "delayed", "role": "ai"}
job = voice.request("Assistant.txt", race)
assert started.wait(3)
incoming = os.path.join(tempstore.mkdtemp("voice_incoming"), "incoming.mp3")
with open(incoming, "wb") as f: f.write(b"ID3received")
voice.receive(voice.file("Assistant.txt", race), incoming)
release.set()
with open(job.result(3), "rb") as f: assert f.read() == b"ID3received"

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

# 官方 /models 实际只列文本模型；语音型号来自公开 schema，绝不向文档站传 Key。
class ModelsResponse(Response):
    def read(self, size):
        return b"""        model:
          type: string
          enum:
            - speech-new-hd
            - speech-new-turbo
            - speech-new-hd
        text:
          description: speech-unrelated
"""
def open_models(req, timeout):
    requests.append(req)
    return ModelsResponse()
with patch("ai_voice._open_url", side_effect=open_models):
    assert ai_voice.list_models(custom) == ["speech-new-hd", "speech-new-turbo"]
assert requests[-1].full_url == ai_voice.MODEL_DOC
assert requests[-1].get_header("Authorization") is None
assert ai_voice.parse_model_doc('{"data":[{"id":"MiniMax-M3"}]}') == []
assert ai_voice.parse_models({"models": ["speech-one", "text-model"]}) == ["speech-one"]
assert ai_voice.parse_models({"data": {"models": [{"model": "speech-two"}]}}) == ["speech-two"]
with patch("ai_voice._open_url", side_effect=OSError("secret-key")):
    try:
        ai_voice.list_models(custom)
        raise AssertionError("失败不应当被当成空列表")
    except ValueError as e:
        assert "secret-key" not in str(e)

# 提示词模式使用 voice_design，克隆把 file_id 作为数字传入，绝不保存成 voice_id。
class DesignResponse(Response):
    def read(self, size):
        return json.dumps({"base_resp": {"status_code": 0}, "voice_id": "designed-voice", "trial_audio": b"ID3demo".hex(),
                           "file": {"file_id": 462298003509468}}).encode()
def open_design(req, timeout):
    requests.append(req)
    return DesignResponse()
with patch("ai_voice_design._open_url", side_effect=open_design):
    vid, audio = ai_voice_design.create(custom, "prompt", "clear voice", "", "", "hello")
    assert (vid, audio) == ("designed-voice", b"ID3demo")
    assert requests[-1].full_url.endswith("/v1/voice_design")
    assert json.loads(requests[-1].data)["prompt"] == "clear voice"
    assert requests[-1].get_header("Authorization") == "Bearer secret-key"
    vid, audio = ai_voice_design.create(custom, "clone", "", "462298003509468", "oit_test_voice", "hello")
    assert vid == "oit_test_voice" and not audio
    assert json.loads(requests[-1].data) == {"file_id": 462298003509468, "voice_id": "oit_test_voice"}
    source = os.path.join(folder, "source.wav")
    with open(source, "wb") as f:
        f.write(b"RIFFtest")
    assert ai_voice_design.upload(custom, source) == "462298003509468"
    assert requests[-1].full_url.endswith("/v1/files/upload")
    assert b'name="purpose"\r\n\r\nvoice_clone' in requests[-1].data
    assert b'RIFFtest\r\n--oit' in requests[-1].data
    for mode, desc, fid, name, words in (("clone", "", "", "valid-name", "hello"), ("prompt", "", "", "valid-name", "hello"),
                                        ("prompt", "voice", "", "462298003509468", "hello")):
        try:
            ai_voice_design.create(custom, mode, desc, fid, name, words)
            raise AssertionError("无效输入必须在请求前拒绝")
        except ValueError:
            pass
class ApiFailure(Response):
    def read(self, size):
        return json.dumps({"base_resp": {"status_code": 2054, "status_msg": "voice id not exist secret-key"}}).encode()
with patch("ai_voice._open_url", return_value=ApiFailure()):
    try:
        ai_voice.synthesize(custom, "hello")
        raise AssertionError("不能吞掉服务商的错误")
    except ValueError as e:
        assert "2054" in str(e) and "voice id not exist" in str(e) and "secret-key" not in str(e)

# 小屏大字号下配置字段仍可滚动到达；音色 ID 可被合成子页替换，Claudia 仍不能删除。
root = tk.Tk()
root.geometry("800x600+5000+5000")
root.update()

# 自动获取只采用当前配置的结果；切换后晚返回的旧列表不能污染新配置。
from pages.model_fetch import auto_models
fetch_dlg = tk.Toplevel(root)
fetch_dlg.withdraw()
fetch_state = {"sig": "old"}
fetch_started, fetch_release = threading.Event(), threading.Event()
applied = []
statuses = []
def fetch_snapshot():
    return fetch_state["sig"], {"sig": fetch_state["sig"]}
def fetch_models(p):
    if p["sig"] == "bad":
        raise OSError("failed")
    if p["sig"] == "old":
        fetch_started.set()
        assert fetch_release.wait(5)
    return [p["sig"]]
schedule = auto_models(fetch_dlg, fetch_snapshot, fetch_models, applied.append, delay=1, status=statuses.append)
def pump_until(fn):
    end = time.monotonic() + 5
    while not fn() and time.monotonic() < end:
        root.update()
        time.sleep(.01)
    assert fn()
schedule()
pump_until(fetch_started.is_set)
fetch_state["sig"] = "new"
schedule()
pump_until(lambda: applied == [["new"]])
fetch_release.set()
settle_at = time.monotonic() + .25
pump_until(lambda: time.monotonic() >= settle_at)
assert applied == [["new"]]
fetch_state["sig"] = "old"
schedule()
pump_until(lambda: applied[-1] == ["old"])
fetch_state["sig"] = "bad"
schedule()
pump_until(lambda: statuses[-1] == "failed")
assert applied[-1] == ["old"]
fetch_dlg.destroy()
c = Comm(tempstore.mkdtemp("voice_comm"), tempstore.mkdtemp("voice_personas"))
c.state_fn = lambda: "idle"
c.voice.data["profiles"][0]["api_key"] = "test-model-key"
orig_wait = Modal.wait
def fake_wait(dlg):
    dlg.update_idletasks()
    def children(w):
        for child in w.winfo_children():
            yield child
            yield from children(child)
    all_ = list(children(dlg))
    assert any(w.get() == config.AI_VOICE_CLAUDIA_ID for w in all_ if isinstance(w, ttk.Entry))
    assert any(w.cget("text") == "音色合成" for w in all_ if isinstance(w, ttk.Button))
    assert any(str(w.cget("text")) == "帮助" for w in all_ if isinstance(w, ttk.Button))
    # 打开已配好的配置会自动取列表，当前型号保持不变。
    model = next(w for w in all_ if isinstance(w, ttk.Combobox) and w.get() == ai_voice.MODELS[0])
    pump_until(lambda: "speech-auto-test" in model.cget("values"))
    assert model.get() == ai_voice.MODELS[0]
    dlg.destroy()
Modal.wait = fake_wait
try:
    config.UI_SCALE = 1.5
    with patch("ai_voice.list_models", return_value=["speech-auto-test"]) as fetch_models_mock:
        voice_dialogs.open_voice_settings(root, c)
    assert fetch_models_mock.call_count == 1
finally:
    Modal.wait = orig_wait
    config.UI_SCALE = 1.0

# 合成子页可切换模式、异步显示实际 voice_id，并应用到父页。
from pages.voice_design import open_voice_design
used = []
def design_wait(dlg):
    def walk(w):
        for child in w.winfo_children():
            yield child
            yield from walk(child)
    widgets = list(walk(dlg))
    radios = {w.cget("text"): w for w in widgets if isinstance(w, ttk.Radiobutton)}
    radios["克隆模式"].invoke()
    radios["提示词模式"].invoke()
    next(w for w in widgets if isinstance(w, tk.Text)).insert("1.0", "clear voice")
    buttons = {w.cget("text"): w for w in widgets if isinstance(w, ttk.Button)}
    buttons["合成音色"].invoke()
    pump_until(lambda: str(buttons["使用此音色"].cget("state")) == "normal")
    assert any(w.get() == "created-voice" for w in widgets if isinstance(w, ttk.Entry))
    buttons["使用此音色"].invoke()
Modal.wait = design_wait
try:
    with patch("ai_voice_design.create", return_value=("created-voice", b"ID3preview")):
        open_voice_design(root, custom, used.append)
    assert used == ["created-voice"]
finally:
    Modal.wait = orig_wait

# 收到消息就生成，清空聊天也清理音频；语音按钮和未播队列能定位较早消息。
c.voice.generate = generate
c.voice.save(enabled=True, auto=True)
hosts = DailyMixin._api_hosts(SimpleNamespace(comm=c))
assert "api.minimaxi.com" in hosts
assert "platform.minimax.io" in hosts
c.voice.save(enabled=False)
assert "api.minimaxi.com" not in DailyMixin._api_hosts(SimpleNamespace(comm=c))
c.voice.save(enabled=True)
c.ai_free_at = lambda now: None
msg = c._mk("ai", "received")
c.messages.append(msg)
wait_for(lambda: os.path.isfile(c.voice.file(c.persona_name(), msg)))
page = CommPage(root, c)
page.pack(fill="both", expand=True)
page.show()
page._voice_scroll(msg)
assert msg["id"] in page._rendered
row = page._rows[page._rendered.index(msg["id"])]
box = next(w for w in row.winfo_children() if isinstance(w, tk.Frame) and w.winfo_children())
bubble = box.winfo_children()[0]
button = next(w for w in bubble.winfo_children() if isinstance(w, tk.Button))
assert button.cget("text") == "▶" and button.winfo_x() <= 3
assert button.winfo_y() > bubble.winfo_children()[0].winfo_y()

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
c.state_fn = lambda: "focus"
assert not c.voice.play_allowed()
page._voice_start()
assert not page._voice_queue
with patch("pages.comm_voice.toast.warn"):
    page._voice_manual(messages[0])
assert voice_calls == [0, 1, 2], "聊天允许时也不能在专注中手动播放"
page._voice_tick()
assert c.voice.play_state == "idle"
c.state_fn = lambda: "break"
assert c.voice.play_allowed()
c.set_viewing(False)
assert c.voice.play_state == "idle"
c.clear()
assert not os.path.isdir(c.voice.folder(c.persona_name()))
page.hide()
root.destroy()
c.flush()
for item in (voice, other, reloaded, c.voice): item.close()
print("Voice check OK")
