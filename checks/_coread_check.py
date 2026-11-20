"""共读聊天桥自检: 认证、阅读会话、人设绑定、聊天上下文和关闭恢复."""

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import sys
import zipfile
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tempstore
import coread_service
from coread_service import OitChatBridge, CoReadService
from comm.comm_coread import COREAD_ACTIVITY_BUDGET, CoReadMixin


class FakeComm:
    def __init__(self):
        self.pid = "persona-one"
        self.reading_mode = False
        self.busy = False
        self.error = ""
        self.sent = []
        self.clock = __import__("datetime").datetime.now()
        self.used_seconds = 0
        self.chat_messages = [{"uid": "message-1", "role": "ai", "text": "Hello", "ts": 1}]

    def now(self):
        return self.clock

    def coread_restricted_seconds(self, _start, _end):
        return self.used_seconds

    def persona_name(self):
        return self.pid

    def share_ai_name(self):
        return "Reader"

    def visible(self):
        return self.chat_messages

    def send(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return ""


class FakeApp:
    def __init__(self):
        self.comm = FakeComm()
        self.pages = {}
        self.bridge = None

    def _end_coread_session(self, _reason=""):
        self.comm.reading_mode = False
        self.bridge.clear_session(_reason)


def request(port, key, path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/{path}", data=data, method=method,
        headers={"x-coread-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main():
    # 模拟第一次部署，确认下载解包与后续查找使用同一目录，不依赖全局 Node。
    if os.name == "nt":
        root = tempstore.mkdtemp(prefix="coread-runtime-")
        def download_runtime(_spec, destination, **_kwargs):
            assert not os.path.isdir(destination), "下载目标必须是文件, 不能用 tempstore.path 创建同名目录"
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            prefix = f"node-v{coread_service.NODE_VERSION}-win-x64/"
            with zipfile.ZipFile(destination, "w") as package:
                package.writestr(prefix + "node.exe", b"local test runtime")
                package.writestr(prefix + "node_modules/npm/bin/npm-cli.js", b"// local test npm")
                package.writestr(prefix + "node_modules/npm/", b"")
                package.writestr(prefix + "LICENSE", b"test license")
        try:
            with patch.object(coread_service.updater, "download_file", side_effect=download_runtime) as download, \
                    patch.object(coread_service.tempstore, "root", return_value=str(Path(root, "temp"))):
                service = CoReadService(root)
                node, npm = service._install_node()
                assert Path(node) == Path(service.node_root) / "node.exe"
                assert Path(node).is_file() and Path(npm).is_file()
                assert service._install_node() == (node, npm)
                assert download.call_count == 1
        finally:
            shutil.rmtree(root, ignore_errors=True)

    class OfferHarness(CoReadMixin):
        def __init__(self, messages):
            self.messages = messages
            self.clock = int(time.time() * 1000)
            self._rt = None

        def _ms(self):
            return self.clock

    free = "我现在没有其他安排"
    harness = OfferHarness([{"role": "me", "text": free, "ts": int(time.time() * 1000)}])
    assert harness._coread_quote_is_recent(free)
    # 这些引文的语义可能互相矛盾；验证器只核对原文和时间，由模型结合上下文判断。
    for text in ("我现在没有其他安排，不过我其实没空", "如果我现在没有其他安排",
                 f"我听你说‘{free}’", f"{free}吗？", f"{free}，如果之后有安排再说"):
        harness.messages = [{"role": "me", "text": text, "ts": harness.clock}]
        assert harness._coread_quote_is_recent(free), text
    harness.messages = [{"role": "ai", "text": free, "ts": harness.clock}]
    assert not harness._coread_quote_is_recent(free)
    harness.messages = [{"role": "me", "text": free, "ts": harness.clock}]
    assert not harness._coread_quote_is_recent("我现在有空")
    harness.messages[0]["ts"] -= 30 * 60 * 1000 + 1
    assert not harness._coread_quote_is_recent(free)
    requested = "我们一起共读吧"
    harness.messages = [{"role": "me", "text": requested, "ts": harness.clock}]
    assert harness._coread_quote_is_recent(requested)
    class ScheduleHarness(OfferHarness):
        def _load_schedule(self): return None
        def _rt_segs(self, *_args, **_kwargs): return []
        def _entries(self):
            return [
                {"kind": "focus", "start": 8 * 60, "end": 9 * 60, "label": "循环专注", "loop": 1},
                {"kind": "focus", "start": 9 * 60, "end": 10 * 60, "label": "单次专注", "loop": None},
                {"kind": "idle", "start": 8 * 60 + 30, "end": 9 * 60 + 30, "label": "晚餐", "loop": None},
                {"kind": "sleep", "start": 23 * 60, "end": 24 * 60, "label": "睡眠", "loop": None},
            ]

    schedule = ScheduleHarness([])
    day = datetime(2026, 1, 1)
    assert schedule.coread_restricted_seconds(day + timedelta(hours=8), day + timedelta(hours=10)) == 90 * 60
    assert schedule.coread_restricted_seconds(day + timedelta(hours=9), day + timedelta(hours=10)) == 30 * 60
    assert schedule.coread_restricted_seconds(day + timedelta(hours=23), day + timedelta(days=1)) == 60 * 60
    origin = datetime(2026, 1, 1)
    seconds = CoReadMixin._coread_merge_seconds([
        (origin, origin + timedelta(seconds=420)),
        (origin + timedelta(seconds=300), origin + timedelta(seconds=600)),
        (origin + timedelta(seconds=700), origin + timedelta(seconds=720)),
    ])
    assert seconds == 620
    assert COREAD_ACTIVITY_BUDGET == 600

    source = ("Title: Verified Book\n[ebook #123]\n\n*** START OF THE PROJECT GUTENBERG EBOOK VERIFIED BOOK ***\n"
              "Complete book text.\n*** END OF THE PROJECT GUTENBERG EBOOK VERIFIED BOOK ***\n")
    service = CoReadService.__new__(CoReadService)
    service._book_lock = threading.Lock()
    service.books = lambda: []
    saved = []
    def request_book(path, method="GET", data=None):
        saved.append((path, method, data))
        return {"ok": True, "book_id": 7, "title": data["title"]}
    service._request = request_book
    def download_book(_spec, destination, **_kwargs):
        with open(destination, "wb") as stream:
            stream.write(source.encode("utf-8"))
    with patch.object(coread_service.updater, "download_file", side_effect=download_book):
        acquired = service.acquire_book("https://www.gutenberg.org/ebooks/123")
        assert acquired["ok"] and acquired["integrity"] == "source_edition_markers_checked"
        assert "SHA-256:" in saved[0][2]["content"] and "Complete book text." in saved[0][2]["content"]
        try:
            service.acquire_book("https://example.com/ebooks/123")
            raise AssertionError("untrusted source was accepted")
        except ValueError:
            pass
        saved.clear()
        try:
            service.acquire_book("https://www.gutenberg.org/ebooks/123", valid=lambda: False)
            raise AssertionError("inactive session was allowed to add a book")
        except ValueError:
            pass
        assert not saved

    app = FakeApp()
    bridge = app.bridge = OitChatBridge(app, "local-test-key")
    bridge.start()
    stopped = threading.Event()

    def pump():
        while not stopped.is_set():
            bridge.pump()
            time.sleep(.005)

    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    try:
        assert request(bridge.port, "wrong", "state")[0] == 401
        status, started = request(bridge.port, "local-test-key", "start", "POST", {})
        assert status == 200 and started["ok"] and started["persona"] == "persona-one", started
        session = started["session_id"]
        assert app.comm.reading_mode
        app.comm.used_seconds = COREAD_ACTIVITY_BUDGET - 1
        assert request(bridge.port, "local-test-key", "heartbeat", "POST", {"session_id": session})[0] == 200
        app.comm.used_seconds = COREAD_ACTIVITY_BUDGET
        status, limited = request(bridge.port, "local-test-key", "heartbeat", "POST", {"session_id": session})
        assert status == 409 and limited["error"] == "reading_time_limit" and not app.comm.reading_mode, (status, limited, app.comm.reading_mode, bridge.end_reason)
        app.comm.used_seconds = 0
        status, started = request(bridge.port, "local-test-key", "start", "POST", {})
        assert status == 200 and started["ok"] and app.comm.reading_mode
        session = started["session_id"]

        status, state = request(bridge.port, "local-test-key", "state")
        assert status == 200 and state["active"] and state["persona_pid"] == "persona-one" and not state["messages"]
        status, state = request(bridge.port, "local-test-key", f"state?session_id={session}")
        assert status == 200 and state["messages"][0]["text"] == "Hello"

        text = "开头" + "请讨论这页原文。" * 3000 + "结尾"
        status, sent = request(bridge.port, "local-test-key", "send", "POST",
                               {"session_id": session, "text": text, "context": "Book, page 3"})
        assert status == 200 and sent["ok"]
        assert app.comm.sent == [(text, {
            "reading_context": "Book, page 3", "from_reader": True})]

        assert request(bridge.port, "local-test-key", "start", "POST", {})[0] == 200
        assert bridge.session_id
        assert request(bridge.port, "local-test-key", "start", "POST",
                       {"persona_pid": "somebody-else"})[0] == 409

        app.comm.pid = "persona-two"
        status, stale = request(bridge.port, "local-test-key", "send", "POST",
                                {"session_id": session, "text": "Do not cross personas"})
        assert status == 409 and stale["error"] == "reading_session_changed"
        assert not app.comm.reading_mode and not app.comm.sent[-1][0].startswith("Do not")

        status, started = request(bridge.port, "local-test-key", "start", "POST", {})
        assert status == 200 and started["persona"] == "persona-two"
        app.comm.chat_messages.append({"uid": "finish-1", "role": "ai", "text": "We can stop here.", "ts": 2})
        app.comm._coread_pending_end = {"session": started["session_id"], "message": "finish-1", "ready": True}
        status, ended = request(bridge.port, "local-test-key", f"state?session_id={started['session_id']}")
        assert status == 200 and ended["active"] is False and ended["messages"][-1]["id"] == "finish-1"
        assert not app.comm.reading_mode and bridge.session_id == ""
        status, started = request(bridge.port, "local-test-key", "start", "POST", {})
        assert status == 200
        status, ended = request(bridge.port, "local-test-key", "end", "POST",
                                {"session_id": started["session_id"]})
        assert status == 200 and ended["ok"] and not app.comm.reading_mode
        print("coread bridge OK")
    finally:
        stopped.set()
        worker.join(timeout=1)
        bridge.close()


if __name__ == "__main__":
    main()
