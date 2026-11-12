"""MiniMax 语音配置、后台生成与本机播放缓存；不依赖沟通模块。"""

import copy
import ctypes
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import Future

import config
import securefile
from ai_assist.ai_key import encrypt_key, decrypt_key
from ai_assist.ai_http import _open_url
from i18n import tr


MODELS = ("speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd", "speech-2.6-turbo",
          "speech-02-hd", "speech-02-turbo", "speech-01-hd", "speech-01-turbo")
MODEL_DOC = "https://platform.minimax.io/docs/api-reference/speech-t2a-http.md"
CN_URL = "https://api.minimaxi.com/v1/t2a_v2"
GLOBAL_URL = "https://api.minimax.io/v1/t2a_v2"
CLAUDIA = "claudia"


def profile(cid="", name="MiniMax"):
    return {"id": cid, "name": name, "template": "minimax", "model": MODELS[0],
            "voice_id": config.AI_VOICE_CLAUDIA_ID if cid == CLAUDIA else "",
            "url": CN_URL, "api_key": ""}


def is_claudia(name):
    return os.path.basename(name).casefold() == "claudia.txt"


def parse_models(data):
    """仅列出语音合成型号，兼容接口返回的对象列表和字符串列表。"""
    rows = data.get("data", data.get("models", []))
    if isinstance(rows, dict):
        rows = rows.get("models", [])
    ids = [row.get("id", row.get("model", "")) if isinstance(row, dict) else row for row in rows]
    return sorted({name for name in ids if isinstance(name, str) and name.startswith("speech-")})


def parse_model_doc(text):
    """只解析官方 T2A 请求结构中的 model 枚举，排除示例和其他接口。"""
    block = re.search(r"(?m)^        model:\s*\n(.*?)(?=^        \w+:)", text, re.S)
    if not block:
        return []
    return list(dict.fromkeys(re.findall(r"(?m)^            - (speech-[\w.-]+)\s*$", block[1])))


def list_models(p):
    """MiniMax 的 /models 只列文本模型；从公开的 T2A schema 获取语音型号。"""
    req = urllib.request.Request(MODEL_DOC)  # 公开文档不附带用户的 Key。
    try:
        with _open_url(req, timeout=20) as r:
            models = parse_model_doc(r.read(2 * 1024 * 1024).decode("utf-8"))
        if not models:
            raise ValueError()
        return models
    except Exception:
        raise ValueError("未能获取语音模型列表，仍可选择预设型号或手动填写.") from None


def synthesize(p, text):
    """同步非流式语音接口，只接受成功响应中的十六进制 MP3。"""
    if not p.get("api_key") or not p.get("voice_id") or not p.get("model"):
        raise ValueError("请先填写语音配置的模型、音色 ID 和 API Key.")
    url = p.get("url", CN_URL).strip()
    if not url.startswith("https://"):
        raise ValueError("语音 API 地址必须以 https:// 开头.")
    body = {"model": p["model"], "text": text, "stream": False, "output_format": "hex",
            "voice_setting": {"voice_id": p["voice_id"]},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1}}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={
        "Authorization": "Bearer " + p["api_key"], "Content-Type": "application/json"})
    # 复用模型请求的代理、拒绝重定向与连接失败回退规则。
    with _open_url(req, timeout=90) as r:
        data = json.loads(r.read(32 * 1024 * 1024))
    if data.get("base_resp", {}).get("status_code", 0) != 0:
        resp = data["base_resp"]
        message = str(resp.get("status_msg", ""))[:300].replace(p["api_key"], "***")
        raise ValueError(tr("语音服务返回错误 {code}：{message}").format(code=resp["status_code"], message=message))
    audio = bytes.fromhex(data.get("data", {}).get("audio", ""))
    if not audio or not (audio.startswith(b"ID3") or audio[:1] == b"\xff"):
        raise ValueError("语音服务没有返回有效音频.")
    return audio


class Voice:
    def __init__(self, data_dir, generate=synthesize):
        self.path = os.path.join(data_dir, "ai_voice.json")
        self.cache = os.path.join(data_dir, "voice_cache")
        self.data = {"ts": 0, "enabled": False, "auto": False,
                     "gap": config.AI_VOICE_GAP, "profiles": [profile(CLAUDIA, "Claudia")], "assign": {}}
        self.lock = threading.RLock()
        self.slots = threading.Semaphore(2)
        self.closed = False
        self.jobs = {}
        self.epochs = {}
        self.generate = generate
        self.error = ""
        self.play_token = 0
        self.play_state = "idle"
        self.play_allowed = lambda: True  # 沟通装配后接入真实状态；独立试听沿用同一门禁。
        self.load()

    def load(self):
        try:
            with securefile.open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            for p in raw.get("profiles", []):
                p["api_key"] = decrypt_key(p.get("api_key", ""))
            self.data.update(raw)
        self._normalize()

    def _normalize(self):
        try:
            gap = float(self.data.get("gap", config.AI_VOICE_GAP))
            self.data["gap"] = max(0, min(60, gap)) if math.isfinite(gap) else config.AI_VOICE_GAP
        except (ValueError, TypeError):
            self.data["gap"] = config.AI_VOICE_GAP
        ps = self.data.get("profiles") or []
        if not any(p.get("id") == CLAUDIA for p in ps):
            ps.insert(0, profile(CLAUDIA, "Claudia"))
        for p in ps:
            if p.get("id") == CLAUDIA and p.get("voice_id", "") in ("", "462298003509468", "oit_claudia_462298003509468"):
                p["voice_id"] = config.AI_VOICE_CLAUDIA_ID
        self.data["profiles"] = ps

    def save(self, **kw):
        with self.lock:
            self.data.update(kw)
            self._normalize()
            self.data["ts"] = max(int(time.time() * 1000), self.data["ts"] + 1)
            self._write()
        if not self.data["enabled"]:
            self.stop()

    def _write(self):
        raw = copy.deepcopy(self.data)
        for p in raw["profiles"]:
            p["api_key"] = encrypt_key(p.get("api_key", ""))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with securefile.open(self.path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)

    def sync_doc(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def apply_doc(self, raw):
        with self.lock:
            if not isinstance(raw, dict) or raw.get("ts", 0) <= self.data["ts"]:
                return
            self.data = copy.deepcopy(raw)
            self._normalize()
            self._write()
        if not self.data["enabled"]:
            self.stop()

    def selected(self, persona):
        cid = self.data["assign"].get(persona, CLAUDIA if is_claudia(persona) else "")
        return next((copy.deepcopy(p) for p in self.data["profiles"] if p["id"] == cid
                     and (cid != CLAUDIA or is_claudia(persona))), None)

    def key(self, persona, m):
        # 音色配置改动不丢掉已生成的旧消息音频。
        return hashlib.sha256((persona + "\0" + str(m.get("uid", m["id"])) + "\0" + m["text"]).encode()).hexdigest()

    def folder(self, persona):
        return os.path.join(self.cache, hashlib.sha256(persona.encode()).hexdigest())

    def file(self, persona, m):
        return os.path.join(self.folder(persona), self.key(persona, m) + ".mp3")

    def played(self, persona, m):
        return os.path.isfile(self.file(persona, m) + ".played")

    def request(self, persona, m):
        if m.get("role") != "ai" or not self.data["enabled"] or self.closed:
            return None
        target = self.file(persona, m)
        with self.lock:
            if os.path.isfile(target):
                return target
            p = self.selected(persona)
            if not p:
                self.error = "请先填写语音配置的模型、音色 ID 和 API Key."
                return None
            if target in self.jobs:
                old = self.jobs[target]
                if not old.done() or old.result():
                    return old
                del self.jobs[target]
            epoch = self.epochs.get(persona, 0)
            self.epochs.setdefault(persona, epoch)
            def work():
                try:
                    audio = self.generate(p, m["text"])
                    with self.lock:
                        if self.closed or epoch != self.epochs.get(persona, 0):
                            return None
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with open(target, "wb") as f:
                            f.write(audio)
                    return target
                except Exception as e:
                    self.error = str(e).replace(p.get("api_key") or "\0", "***") if isinstance(e, ValueError) else "语音生成失败，请检查网络和语音配置."
                    return None
            job = Future()
            self.jobs[target] = job
            def run():
                with self.slots:
                    with self.lock:
                        valid = not self.closed and epoch == self.epochs.get(persona, 0)
                    job.set_result(work() if valid else None)
            threading.Thread(target=run, name="oit-voice", daemon=True).start()
            return job

    def autoplay(self, persona, messages):
        return [m for m in messages if m["role"] == "ai" and not self.played(persona, m)][-config.AI_VOICE_AUTO_COUNT:]

    @staticmethod
    def _mci(command):
        buf = ctypes.create_unicode_buffer(256)
        err = ctypes.windll.winmm.mciSendStringW(command, buf, len(buf), None)
        if err:
            raise OSError("MCI")
        return buf.value

    def stop(self):
        with self.lock:
            self.play_token += 1
            self.play_state = "idle"
            try:
                self._mci("close oit_ai_voice")
            except (OSError, AttributeError):
                pass

    def play(self, persona, m):
        self.stop()
        if not self.play_allowed():
            self.error = "仅可在空闲或休息期间播放语音."
            return
        token = self.play_token
        self.play_state = "loading"
        self.error = ""
        job = self.request(persona, m)
        def run():
            try:
                path = job.result() if hasattr(job, "result") else job
                with self.lock:
                    if token != self.play_token:
                        return
                    if not self.play_allowed():
                        self.stop()
                        return
                    if not path or not self.data["enabled"]:
                        self.play_state = "failed"
                        return
                    self._mci(f'open "{path}" type mpegvideo alias oit_ai_voice')
                    self._mci("play oit_ai_voice")
                    # 播放器成功开始后才记为播放过。
                    with open(path + ".played", "wb"):
                        pass
                    self.play_state = "playing"
                while token == self.play_token:
                    time.sleep(0.1)
                    with self.lock:
                        if token != self.play_token:
                            return
                        if not self.play_allowed():
                            self.stop()
                            return
                        if self._mci("status oit_ai_voice mode") != "playing":
                            self._mci("close oit_ai_voice")
                            self.play_state = "done"
                            return
            except Exception:
                if token == self.play_token:
                    self.error = "语音播放失败."
                    self.play_state = "failed"
        threading.Thread(target=run, daemon=True).start()

    def clear(self, persona):
        import shutil
        self.stop()
        with self.lock:
            self.epochs[persona] = self.epochs.get(persona, 0) + 1
            prefix = self.folder(persona) + os.sep
            self.jobs = {k: v for k, v in self.jobs.items() if not k.startswith(prefix)}
            shutil.rmtree(self.folder(persona), ignore_errors=True)

    def close(self):
        self.stop()
        with self.lock:
            for name in list(self.epochs):
                self.epochs[name] += 1
            self.closed = True
