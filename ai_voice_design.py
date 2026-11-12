"""MiniMax 提示词音色设计、录音上传与克隆；使用当前语音配置的同源接口。"""

import json
import os
import re
import urllib.request
import uuid
from urllib.parse import urlsplit, urlunsplit

from ai_assist.ai_http import _open_url
from i18n import tr


def endpoint(p, operation):
    url = urlsplit(p.get("url", "").strip())
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError(tr("语音 API 地址必须以 https:// 开头."))
    path = url.path.rstrip("/").removesuffix("/t2a_v2") + "/" + operation
    return urlunsplit((url.scheme, url.netloc, path, url.query, ""))


def call(p, operation, body, content_type="application/json"):
    key = p.get("api_key", "").strip()
    if not key:
        raise ValueError(tr("请先填写 API Key."))
    req = urllib.request.Request(endpoint(p, operation), data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": content_type})
    with _open_url(req, timeout=90) as r:
        data = json.loads(r.read(32 * 1024 * 1024))
    resp = data.get("base_resp", {})
    if resp.get("status_code", 0) != 0:
        raise ValueError(tr("语音服务返回错误 {code}：{message}").format(
            code=resp["status_code"], message=str(resp.get("status_msg", ""))[:300].replace(key, "***")))
    return data


def upload(p, path):
    if os.path.splitext(path)[1].lower() not in (".mp3", ".m4a", ".wav") or not 0 < os.path.getsize(path) <= 20 * 1024 * 1024:
        raise ValueError(tr("请选择不超过 20 MB 的 mp3、m4a 或 wav 录音（10 秒至 5 分钟）."))
    boundary = "oit" + uuid.uuid4().hex
    # 文件名只用固定 ASCII，避免本地路径或换行进入请求头。
    filename = "voice" + os.path.splitext(path)[1].lower()
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode()
    with open(path, "rb") as f:
        body = head + f.read(20 * 1024 * 1024 + 1) + f"\r\n--{boundary}--\r\n".encode()
    data = call(p, "files/upload", body, "multipart/form-data; boundary=" + boundary)
    file_id = str(data.get("file", {}).get("file_id", ""))
    if not file_id.isdigit():
        raise ValueError(tr("语音服务没有返回有效的 file_id."))
    return file_id


def create(p, mode, prompt, file_id, voice_id, preview_text):
    """返回实际 voice_id 和可选的试听 MP3；file_id 从不当作 voice_id 保存。"""
    voice_id = voice_id.strip() or "oit_voice_" + uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]", voice_id):
        raise ValueError(tr("新音色 ID 须为 8–256 位，以英文字母开头，仅含字母、数字、横线或下划线，末尾为字母或数字."))
    if mode == "prompt":
        if not prompt.strip() or not preview_text.strip() or len(preview_text) > 500:
            raise ValueError(tr("请填写声音描述和不超过 500 字的试听文本."))
        body = {"prompt": prompt.strip(), "preview_text": preview_text.strip(), "voice_id": voice_id}
        data = call(p, "voice_design", json.dumps(body).encode("utf-8"))
        actual = data.get("voice_id")
        if not actual:
            raise ValueError(tr("语音服务没有返回有效的 voice_id."))
        audio = bytes.fromhex(data.get("trial_audio", ""))
        return actual, audio
    if mode != "clone" or not file_id.strip().isdigit():
        raise ValueError(tr("请上传录音或填写有效的 file_id."))
    body = {"file_id": int(file_id.strip()), "voice_id": voice_id}
    call(p, "voice_clone", json.dumps(body).encode("utf-8"))
    return voice_id, b""
