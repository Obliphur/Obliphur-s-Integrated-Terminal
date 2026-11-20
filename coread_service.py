# -*- coding: utf-8 -*-
"""coread 本地服务的部署、生命周期和受密钥保护的 OIT 工具客户端。"""

import json
import http.server
import hashlib
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime

import config
import tempstore
import updater
from i18n import tr


COREAD_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "third_party", "coread")
NODE_VERSION = "24.22.3"
NODE_ARCHIVE = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"
NODE_SHA256 = "e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f"
NODE_HOSTS = {"nodejs.org"}
NODE_MAX_DOWNLOAD = 50 * 1024 * 1024
COREAD_HTTP_PORTS = range(3000, 3010)
API_TIMEOUT = 15
BRIDGE_BODY_MAX = 24 * 1024
BRIDGE_CHAT_BODY_MAX = 4 * 1024 * 1024  # 共读聊天的 HTTP 请求大小保护，包含正文和阅读上下文


def _copy_source(source: str, target: str):
    """把发布包附带的共读源码同步到用户数据目录, 保留 npm 依赖和书库."""
    names = {"LICENSE", "README.md", "OIT-INTEGRATION.txt", "package.json", "package-lock.json",
             "server.mjs", "mcp-stdio.mjs", "mcp-sse.mjs", "vite.config.ts"}
    for directory in ("lib", "web", "public", "examples"):
        src = os.path.join(source, directory)
        dst = os.path.join(target, directory)
        if os.path.isdir(src):
            for root, dirs, files in os.walk(src):
                dirs[:] = [name for name in dirs if name not in ("node_modules", "test-results")]
                rel = os.path.relpath(root, src)
                out = dst if rel == "." else os.path.join(dst, rel)
                os.makedirs(out, exist_ok=True)
                for filename in files:
                    shutil.copy2(os.path.join(root, filename), os.path.join(out, filename))
    os.makedirs(target, exist_ok=True)
    for name in names:
        src = os.path.join(source, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target, name))


def _node_paths(root: str):
    node = os.path.join(root, "node.exe")
    npm = os.path.join(root, "node_modules", "npm", "bin", "npm-cli.js")
    return node, npm


class CoReadService:
    """管理本机 Node.js 服务, 同一局域网内的已配对 OIT 设备共用一份书库."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.root = os.path.join(data_dir, "coread")
        self.project = os.path.join(self.root, "app")
        self.db_dir = os.path.join(self.root, "data")
        self.node_root = os.path.join(self.root, "node")
        self.config_path = os.path.join(self.root, "service.json")
        self.log_path = os.path.join(self.root, "server.log")
        self._lock = threading.RLock()
        self._book_lock = threading.Lock()
        self.state = "stopped"
        self.error = ""
        self.progress = ""
        self.process = None
        self.port = 0
        self.access_key = ""
        self.bridge_port = 0
        self.on_ready = None
        self.last_reader_heartbeat = 0.0
        self._load()
        self.refresh()

    def _load(self):
        saved = config.load_json_file(self.config_path, {})
        self.access_key = str(saved.get("access_key") or secrets.token_urlsafe(32))
        if not saved.get("access_key"):
            config.save_json_file(self.config_path, {"access_key": self.access_key})
        self.port = int(saved.get("port") or 3000)

    def _save(self):
        config.save_json_file(self.config_path, {"access_key": self.access_key, "port": self.port})

    def refresh(self):
        with self._lock:
            if self.process is not None:
                code = self.process.poll()
                if code is None:
                    self.state, self.error = "ready", ""
                    return self.state
                self.process = None
                self.state = "error" if code else "stopped"
                if code:
                    self.error = tr("共读服务已退出 (代码 {code})", code=code)
            return self.state

    def url(self, host="127.0.0.1"):
        return f"http://{host}:{self.port}/?owner_key={self.access_key}&access_key={self.access_key}"

    def start(self):
        with self._lock:
            self.refresh()
            if self.state in ("installing", "starting", "ready"):
                return False
            source = COREAD_SOURCE
            if not os.path.isfile(os.path.join(source, "server.mjs")):
                raise RuntimeError(tr("安装包里没有共读组件."))
            # 每次启动都更新程序代码; 用户书库和 node_modules 在别处保留.
            _copy_source(source, self.project)
            self.state, self.error, self.progress = "starting", "", tr("准备 coread 服务…")
            threading.Thread(target=self._start_worker, daemon=True, name="OIT-coread").start()
            return True

    def open(self, on_ready=None):
        """启动服务并在就绪后打开共读网页."""
        state = self.refresh()
        if state == "ready":
            if on_ready:
                on_ready()
            return False
        if state in ("installing", "starting"):
            return True
        self.on_ready = on_ready
        return self.start()

    def _install_node(self):
        node, npm = _node_paths(self.node_root)
        if os.path.isfile(node) and os.path.isfile(npm):
            return node, npm
        if os.name != "nt" or (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")).lower() not in ("amd64", "x86_64"):
            raise RuntimeError(tr("自动安装 Node.js 需要 Windows x64."))
        os.makedirs(self.root, exist_ok=True)
        stage = tempstore.mkdtemp("update", prefix="coread-node-")
        archive = os.path.join(stage, f"node-v{NODE_VERSION}-win-x64.zip")
        try:
            self.progress = tr("下载 Node.js…")
            updater.download_file({"url": NODE_ARCHIVE, "accept": updater.ACCEPT_FILE,
                                   "sha256": NODE_SHA256}, archive, allowed_hosts=NODE_HOSTS,
                                  max_download=NODE_MAX_DOWNLOAD)
            with zipfile.ZipFile(archive) as package:
                for info in package.infolist():
                    if info.is_dir():
                        continue
                    clean = os.path.normpath(info.filename).replace("/", os.sep)
                    if os.path.isabs(clean) or clean == ".." or clean.startswith(".." + os.sep):
                        raise RuntimeError("Node.js 安装包包含无效路径")
                    unix_name = info.filename.replace("\\", "/")
                    if not (unix_name.endswith("/node.exe") or unix_name.endswith("/LICENSE")
                            or unix_name.endswith("/LICENSES.md") or "node_modules/npm/" in unix_name):
                        continue
                    target = os.path.join(stage, clean)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with package.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            source_root = os.path.join(stage, f"node-v{NODE_VERSION}-win-x64")
            os.makedirs(self.node_root, exist_ok=True)
            shutil.copy2(os.path.join(source_root, "node.exe"), os.path.join(self.node_root, "node.exe"))
            for name in ("LICENSE", "LICENSES.md"):
                path = os.path.join(source_root, name)
                if os.path.isfile(path):
                    shutil.copy2(path, os.path.join(self.node_root, name))
            npm_src = os.path.join(source_root, "node_modules")
            npm_dst = os.path.join(self.node_root, "node_modules")
            if os.path.isdir(npm_dst):
                shutil.rmtree(npm_dst)
            os.replace(npm_src, npm_dst)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        node, npm = _node_paths(self.node_root)
        if not os.path.isfile(node) or not os.path.isfile(npm):
            raise RuntimeError(tr("Node.js 安装包不完整."))
        return node, npm

    def _start_worker(self):
        process = None
        try:
            node, npm = self._install_node()
            self.progress = tr("安装共读服务组件…")
            for port in COREAD_HTTP_PORTS:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                        probe.bind(("0.0.0.0", port))
                    self.port = port
                    break
                except OSError:
                    continue
            else:
                raise RuntimeError(tr("没有可用的共读服务端口."))
            self._save()
            env = dict(os.environ)
            env["PATH"] = os.path.dirname(node) + os.pathsep + env.get("PATH", "")
            env.update({"COREAD_DB": os.path.join(self.db_dir, "coread.db"),
                        "ROOM_LOCK_PATH": os.path.join(self.db_dir, "reading-room-lock.json"),
                        "ROOM_OWNER_KEY": self.access_key,
                        "COREAD_ACCESS_KEY": self.access_key,
                        "COREAD_OIT_BRIDGE_PORT": str(self.bridge_port),
                        "COREAD_HOST": "0.0.0.0", "COREAD_PORT": str(self.port),
                        "npm_config_audit": "false", "npm_config_fund": "false"})
            os.makedirs(self.db_dir, exist_ok=True)
            stamp = os.path.join(self.project, ".oit-install-stamp")
            lockfile = os.path.join(self.project, "package-lock.json")
            with open(lockfile, "rb") as source_file:
                digest = hashlib.sha256(source_file.read()).hexdigest()
            if not os.path.isdir(os.path.join(self.project, "node_modules")) or not os.path.isfile(stamp) or open(stamp, encoding="ascii").read() != digest:
                result = subprocess.run([node, npm, "ci", "--omit=dev", "--no-audit", "--no-fund"],
                                         cwd=self.project, env=env, capture_output=True, timeout=900,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    detail = (result.stderr or result.stdout).decode("utf-8", "replace")[-1800:]
                    raise RuntimeError(tr("安装共读服务依赖失败: {error}", error=detail.strip()))
                with open(stamp, "w", encoding="ascii") as out:
                    out.write(digest)
            self.progress = tr("启动共读服务…")
            with open(self.log_path, "ab") as log:
                process = subprocess.Popen([node, os.path.join(self.project, "server.mjs")],
                                           cwd=self.project, env=env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                           close_fds=True)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(tr("共读服务启动失败，请查看 OIT 日志."))
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=.4):
                        break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError(tr("共读服务启动超时."))
            with self._lock:
                self.process, self.state, self.error, self.progress = process, "ready", "", ""
            if self.on_ready:
                callback, self.on_ready = self.on_ready, None
                callback()
        except Exception as exc:
            if process is not None and process.poll() is None:
                process.terminate()
            with self._lock:
                self.process, self.state = None, "error"
                self.error = str(exc) or type(exc).__name__
                self.progress = ""

    def stop(self):
        with self._lock:
            process, self.process = self.process, None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
            self.state, self.progress = "stopped", ""
            self.on_ready = None

    def lan_hosts(self):
        try:
            from devsync.devsync_util import _ipv4_nets
            return [addr for addr, _mask in _ipv4_nets()
                    if not addr.startswith(("127.", "169.254.")) and not addr.startswith("0.")]
        except Exception:
            try:
                return [socket.gethostbyname(socket.gethostname())]
            except OSError:
                return []

    def _request(self, path, method="GET", data=None):
        if not self.refresh() == "ready":
            raise RuntimeError(tr("本机共读服务没有运行."))
        url = f"http://127.0.0.1:{self.port}{path}"
        payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method=method,
                                         headers={"Content-Type": "application/json", "x-coread-key": self.access_key,
                                                  "x-owner-key": self.access_key})
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
                return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read(2048).decode("utf-8", "replace")
            raise RuntimeError(tr("共读服务请求失败: HTTP {code} {message}", code=exc.code, message=message)) from exc

    def books(self):
        return self._request("/v1/books").get("books", [])

    def acquire_book(self, source_url, valid=lambda: True):
        """仅从 Gutenberg 官方取全文；校验失败不写库，出处随正文备份。"""
        source = urllib.parse.urlsplit(str(source_url))
        match = re.fullmatch(r"/ebooks/([1-9]\d{0,6})/?", source.path)
        if (source.scheme != "https" or source.netloc not in ("www.gutenberg.org", "gutenberg.org")
                or source.query or source.fragment or not match):
            raise ValueError("请提供 Project Gutenberg 官方书目地址 https://www.gutenberg.org/ebooks/编号；其他来源尚不能自动核验")
        book_id = match[1]
        canonical = f"https://www.gutenberg.org/ebooks/{book_id}"
        download = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
        with self._book_lock:
            for book in self.books():
                first = self._request(f"/v1/books/{int(book['id'])}?page=1").get("paragraphs", [])
                if first and str(first[0].get("content", "")).startswith(f"OIT 书籍来源: {canonical}\n"):
                    return {"ok": True, "book_id": book["id"], "title": book["title"], "source_url": canonical, "existing": True}
            stage = tempstore.mkdtemp("misc", prefix="coread-book-")
            try:
                filename = os.path.join(stage, "book.txt")
                updater.download_file({"url": download, "accept": updater.ACCEPT_FILE}, filename,
                                      allowed_hosts={"www.gutenberg.org", "gutenberg.org"},
                                      max_download=config.COREAD_BOOK_MAX_BYTES, workers=1)
                with open(filename, "rb") as stream:
                    raw = stream.read(config.COREAD_BOOK_MAX_BYTES + 1)
                if len(raw) > config.COREAD_BOOK_MAX_BYTES:
                    raise ValueError("书籍文件超过大小上限")
                text = raw.decode("utf-8-sig").replace("\r\n", "\n")
                start = re.search(r"^\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .+?\*\*\*\s*$", text, re.M)
                end = re.search(r"^\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .+?\*\*\*\s*$", text, re.M)
                header = text[:start.start()] if start else ""
                title = re.search(r"^Title:\s*(.+)$", header, re.M)
                identity = re.search(r"\[\s*e-?book\s*#\s*" + book_id + r"\s*\]", header, re.I)
                if not start or not end or end.start() <= start.end() or not title or not identity:
                    raise ValueError("未通过全文校验：缺少对应书号、书名或完整首尾标记")
                content = text[start.end():end.start()].strip()
                if not content or "\x00" in content or re.search(r"<(?:!doctype|html)\b", content, re.I):
                    raise ValueError("下载结果不是可验证的完整书籍文本")
                digest = hashlib.sha256(raw).hexdigest()
                provenance = (f"OIT 书籍来源: {canonical}\n下载地址: {download}\nSHA-256: {digest}\n"
                              "校验: 官方 HTTPS 来源、书号、UTF-8 文本及全文首尾标记。完整性限于此来源版本，不保证与其他版本一致。\n"
                              "授权: Project Gutenberg 电子书；具体使用条件见文件末尾许可。")
                if not valid():
                    raise ValueError("共读会话已结束，未加入书库")
                result = self._request("/v1/books", "POST", {"title": title[1].strip(), "content": provenance + "\n\n" + text})
                return {**result, "source_url": canonical, "sha256": digest, "integrity": "source_edition_markers_checked"}
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def read_book(self, book_id, page=1):
        book_id, page = int(book_id), int(page)
        if book_id < 1 or page < 1:
            raise ValueError("书籍编号和页码必须大于零")
        data = self._request(f"/v1/books/{book_id}?page={page}")
        if data.get("error"):
            raise ValueError(str(data["error"]))
        paragraphs = data.get("paragraphs") or []
        comments = data.get("comments") or []
        page_data = data.get("pagination") or {}
        out = [f"书名: {data.get('book', {}).get('title', '')} | 第 {page_data.get('page', page)} / {page_data.get('totalPages', '?')} 页"]
        out.extend(f"[{item.get('idx')}] {item.get('content', '')}" for item in paragraphs)
        out.extend(f"批注 [{item.get('from_who', 'human')}@{item.get('paragraph_idx')}]: {item.get('content', '')}"
                   for item in comments)
        text = "\n\n".join(out)
        return text[:18000]

    def add_comment(self, book_id, paragraph_idx, content, selected_text="", author="AI"):
        book_id, paragraph_idx = int(book_id), int(paragraph_idx)
        content = str(content or "").strip()
        if book_id < 1 or paragraph_idx < 0 or not content or len(content) > 5000:
            raise ValueError("批注参数无效")
        data = {"paragraph_idx": paragraph_idx, "content": content, "from_who": str(author or "AI")[:80]}
        if selected_text:
            data["selected_text"] = str(selected_text)[:3000]
        result = self._request(f"/v1/books/{book_id}/comment", "POST", data)
        if result.get("error") or result.get("ok") is False:
            raise ValueError(str(result.get("error") or "添加批注失败"))
        return {"ok": True, "id": result.get("id")}


def manager(data_dir):
    return CoReadService(data_dir)


class OitChatBridge:
    """仅本机使用的 HTTP 桥, 把网页聊天请求安全地交给 OIT 界面线程."""

    def __init__(self, app, access_key):
        self.app = app
        self.access_key = access_key
        self.requests = queue.Queue()
        self._server = None
        self._thread = None
        self.session_id = ""
        self.session_pid = ""
        self.session_started_at = None
        self.end_reason = ""

    @property
    def port(self):
        return int(self._server.server_address[1]) if self._server else 0

    def start(self):
        if self._server:
            return
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, _fmt, *_args):
                return

            def _send(self, status, payload):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self):
                if not secrets.compare_digest(self.headers.get("x-coread-key", ""), bridge.access_key):
                    self._send(401, {"error": "access_denied"})
                    return
                parsed = urllib.parse.urlsplit(self.path)
                op = "state" if self.command == "GET" else parsed.path.strip("/")
                body = {}
                if self.command == "POST":
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        limit = BRIDGE_CHAT_BODY_MAX if op == "send" else BRIDGE_BODY_MAX
                        if size < 0 or size > limit:
                            self._send(413, {"error": "request_too_large"})
                            return
                        body = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
                        if not isinstance(body, dict):
                            raise ValueError
                    except (ValueError, UnicodeError, json.JSONDecodeError):
                        self._send(400, {"error": "invalid_json"})
                        return
                if self.command == "GET":
                    body = {"session_id": urllib.parse.parse_qs(parsed.query).get("session_id", [""])[0]}
                if op not in ("state", "start", "heartbeat", "send", "end", "acquire"):
                    self._send(404, {"error": "not_found"})
                    return
                item = {"op": op, "body": body, "done": threading.Event()}
                bridge.requests.put(item)
                if not item["done"].wait(180 if op == "acquire" else 20):
                    self._send(503, {"error": "oit_unavailable"})
                    return
                if op == "acquire" and item.get("result", {}).get("ok"):
                    try:
                        result = bridge.app.coread_service.acquire_book(body.get("source_url", ""),
                            valid=lambda: bridge._matches_session(bridge.app.comm, body))
                        self._send(200, result)
                    except Exception as exc:
                        self._send(400, {"ok": False, "error": str(exc)[:500]})
                else:
                    self._send(item.get("status", 200), item.get("result") or {})

            do_GET = _handle
            do_POST = _handle

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="OIT-coread-bridge")
        self._thread.start()

    def pump(self):
        """在 Tk 主线程调用, 避免 HTTP 工作线程改动沟通状态."""
        while True:
            try:
                item = self.requests.get_nowait()
            except queue.Empty:
                break
            try:
                comm = self.app.comm
                if item["op"] == "state":
                    if (getattr(comm, "reading_mode", False) and self.session_pid
                            and str(comm.pid) != self.session_pid):
                        self.app._end_coread_session()
                    active = bool(getattr(comm, "reading_mode", False) and self.session_pid
                                  and str(comm.pid) == self.session_pid)
                    if active and not self._within_activity_budget(comm):
                        active = False
                    supplied = str(item["body"].get("session_id") or "")
                    if supplied and (not active or not secrets.compare_digest(supplied, self.session_id)):
                        item["status"], item["result"] = 409, self._inactive_result()
                        continue
                    messages = ([{"id": m.get("uid", m.get("id")), "role": m["role"],
                                  "text": str(m.get("text") or ""), "ts": int(m.get("ts") or 0)}
                                 for m in comm.visible()[-80:]] if supplied and active else [])
                    pending_end = getattr(comm, "_coread_pending_end", None)
                    ending = bool(active and pending_end and pending_end.get("ready")
                                  and pending_end.get("session") == self.session_id
                                  and any(m.get("id") == pending_end.get("message") for m in messages))
                    if ending:
                        item["result"] = {"active": False, "session_id": "", "end_reason": "Claudia 已结束这次共同阅读。",
                                           "persona": comm.persona_name(), "persona_pid": str(comm.pid), "name": comm.share_ai_name(),
                                           "busy": bool(comm.busy), "error": str(comm.error or ""), "messages": messages}
                        self.app._end_coread_session()
                        continue
                    item["result"] = {
                        "active": active,
                        "session_id": self.session_id if active else "",
                        "end_reason": self.end_reason if not active else "",
                        "persona": comm.persona_name(), "persona_pid": str(comm.pid), "name": comm.share_ai_name(),
                        "busy": bool(comm.busy), "error": str(comm.error or ""),
                        "messages": messages,
                    }
                elif item["op"] == "start":
                    if not str(comm.pid or ""):
                        item["status"], item["result"] = 409, {"ok": False, "error": "persona_required"}
                        continue
                    requested_pid = str(item["body"].get("persona_pid") or "")
                    if requested_pid and not secrets.compare_digest(requested_pid, str(comm.pid)):
                        item["status"], item["result"] = 409, {"ok": False, "error": "persona_mismatch",
                                                                  "message": "OIT 当前聊天对象与阅读设备不一致。"}
                        continue
                    if getattr(comm, "reading_mode", False):
                        if self.session_pid and secrets.compare_digest(self.session_pid, str(comm.pid)):
                            self.last_reader_heartbeat = time.monotonic()
                            item["result"] = {"ok": True, "session_id": self.session_id,
                                               "persona": comm.persona_name()}
                        else:
                            item["status"], item["result"] = 409, {"ok": False, "error": "reading_already_active"}
                        continue
                    self.session_id = secrets.token_urlsafe(24)
                    self.session_pid = str(comm.pid)
                    self.session_started_at = comm.now() if hasattr(comm, "now") else datetime.now()
                    self.end_reason = ""
                    comm.reading_mode = True
                    comm.coread_session_id = self.session_id
                    self.last_reader_heartbeat = time.monotonic()
                    page = getattr(self.app, "pages", {}).get("comm")
                    if page is not None:
                        page.set_reading_mode(True)
                    item["result"] = {"ok": True, "session_id": self.session_id,
                                       "persona": comm.persona_name()}
                elif item["op"] in ("heartbeat", "acquire"):
                    if not self._matches_session(comm, item["body"]):
                        item["status"], item["result"] = 409, self._inactive_result()
                    else:
                        self.last_reader_heartbeat = time.monotonic()
                        item["result"] = {"ok": True, "active": True}
                elif item["op"] == "send":
                    if not self._matches_session(comm, item["body"]):
                        item["status"], item["result"] = 409, self._inactive_result()
                    else:
                        text = str(item["body"].get("text") or "").strip()
                        context = str(item["body"].get("context") or "")[:18000]
                        if not text:
                            item["status"], item["result"] = 400, {"error": "message_required"}
                        else:
                            why = comm.send(text, reading_context=context, from_reader=True)
                            item["result"] = {"ok": not bool(why), "error": why or ""}
                else:
                    if not self._matches_session(comm, item["body"]):
                        item["status"], item["result"] = 409, self._inactive_result()
                    else:
                        self.app._end_coread_session()
                        item["result"] = {"ok": True}
            except Exception as exc:
                item["status"], item["result"] = 500, {"error": str(exc) or type(exc).__name__}
            finally:
                item["done"].set()

    def _matches_session(self, comm, body):
        """确认网页仍属于开始阅读时选择的人设和会话."""
        active = bool(getattr(comm, "reading_mode", False) and self.session_pid
                      and str(comm.pid) == self.session_pid)
        supplied = str((body or {}).get("session_id") or "")
        if active and supplied and secrets.compare_digest(supplied, self.session_id):
            return self._within_activity_budget(comm)
        if getattr(comm, "reading_mode", False) and self.session_pid and not active:
            self.app._end_coread_session()
        return False

    def _within_activity_budget(self, comm):
        if not self.session_started_at:
            return False
        used = comm.coread_restricted_seconds(self.session_started_at, comm.now())
        from comm.comm_coread import COREAD_ACTIVITY_BUDGET
        if used < COREAD_ACTIVITY_BUDGET:
            return True
        self.app._end_coread_session(tr("本次共读已达到预留活动重叠 10 分钟的上限。"))
        return False

    def _inactive_result(self):
        return {"ok": False, "active": False,
                "error": "reading_time_limit" if self.end_reason else "reading_session_changed",
                "message": self.end_reason or "阅读会话已结束。"}

    def clear_session(self, reason=""):
        self.session_id = ""
        self.session_pid = ""
        self.session_started_at = None
        self.last_reader_heartbeat = 0.0
        self.end_reason = str(reason or "")

    def watchdog(self):
        """阅读网页消失后自动解除沟通页锁定."""
        comm = self.app.comm
        if not getattr(comm, "reading_mode", False):
            return
        if not self.session_pid or str(comm.pid) != self.session_pid:
            self.app._end_coread_session()
            return
        if not self._within_activity_budget(comm):
            return
        if time.monotonic() - self.last_reader_heartbeat > 15:
            self.app._end_coread_session()

    def close(self):
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()
        self._thread = None
