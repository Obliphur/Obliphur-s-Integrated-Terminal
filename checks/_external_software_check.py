"""外部软件部署：只用临时目录和安装器替身，不安装真实软件。"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("external_software_check")

import configparser
import fnmatch
import io
import hashlib
from pathlib import Path
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch
import urllib.request

import config
import external_software as E
import piano_model as P
import i18n
from pages.settings_page import SettingsPage
from settings_manager import SettingsManager
from comm import Comm


base = Path(tempstore.mkdtemp("misc", prefix="external-check-"))
root_dir = base / "外部软件 with spaces"
root_dir.mkdir()
u = E.ExternalSoftware(str(base / "data"))
try:
    u.select_root("relative")
    raise AssertionError("不能保存相对路径")
except ValueError:
    pass
u.select_root(str(root_dir))
assert E.ExternalSoftware(str(base / "data")).root == str(root_dir)

for url in ("http://bitsum.com/x", "https://bitsum.com.evil.test/x", "https://evil.test/x", "https://user@bitsum.com/x"):
    assert not E._official_url(url)
    try:
        E._Redirect().redirect_request(urllib.request.Request("https://bitsum.com/x"), None, 302, "", {}, url)
        raise AssertionError("不能重定向到非官方地址")
    except ValueError:
        pass

class Response(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def geturl(self):
        return "https://dl.bitsum.com/files/processlassosetup64.exe"


download = base / "installer.exe"
for content, size in ((b"MZ" + b"x" * 110000, None), (b"MZ" * 55000, 200000), (b"<html>" * 20000, None)):
    with patch("updater.upstream_proxy", return_value=None), patch("urllib.request.build_opener") as op:
        op.return_value.open.return_value = Response(content, size)
        try:
            E.download_installer(str(download))
            assert content.startswith(b"MZ") and size is None
        except ValueError:
            assert size is not None or not content.startswith(b"MZ")

valid = b"Valid|CN=Bitsum Technologies (Bitsum LLC), O=Bitsum Technologies (Bitsum LLC), C=US"
for output, expected in ((valid, True), (valid.replace(b"Valid|", b"NotTrusted|"), False),
                         (b"Valid|CN=Bitsum, O=Not Bitsum LLC, C=US", False)):
    with patch.object(E.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
        assert E.signature_ok(str(download)) == expected

target = root_dir / "Process Lasso"
calls, work_dirs = [], []

def fake_download(path, progress):
    Path(path).write_bytes(b"MZ")
    work_dirs.append(Path(path).parent)
    progress(100)


def fake_install(command, **kwargs):
    calls.append(command)
    assert command.endswith(" /D=" + str(target))
    assert "/governor_start_type=service" in command
    assert "shell" not in kwargs
    for name in ("ProcessLasso.exe", "ProcessGovernor.exe"):
        (target / name).write_bytes(b"MZ")
    return subprocess.CompletedProcess(command, 0)


with patch.object(E, "download_installer", fake_download), patch.object(E, "signature_ok", return_value=False), \
        patch.object(E.subprocess, "run", fake_install):
    u._deploy(str(target))
    assert u.state == "error" and not calls and not u.installed
    assert not (target / "oit-config/prolasso.ini").exists()
assert all(not d.exists() for d in work_dirs)

with patch.object(E, "download_installer", fake_download), patch.object(E, "signature_ok", return_value=True), \
        patch.object(E.subprocess, "run", fake_install):
    u._deploy(str(target))
assert u.state == "ready" and u.installed and len(calls) == 1
assert E.ExternalSoftware(str(base / "data")).installed
assert all(not d.exists() for d in work_dirs)
ini = configparser.ConfigParser(interpolation=None)
ini.read(target / "oit-config/prolasso.ini", encoding="utf-16")
for name in ("python.exe", "pythonw.exe", "python3.13.exe", "torchrun.exe", "oit.exe", "oitlink.exe"):
    for section, key in (("OutOfControlProcessRestraint", "OocExclusions"), ("MemoryManagement", "SmartTrimExclusions")):
        assert any(fnmatch.fnmatch(name, pat) for pat in ini[section][key].split(",")), name
assert ini["MemoryManagement"].getboolean("SmartTrimIsEnabled")
assert not ini["MemoryManagement"].getboolean("SmartTrimClearStandbyList")
assert not ini["MemoryManagement"].getboolean("SmartTrimClearFileCache")
assert "DefaultAffinities" not in str(dict(ini)) and "HardThrottle" not in str(dict(ini))

# 忙碌期间第二次点击不重复安装，也不能修改目标目录。
started, finish = threading.Event(), threading.Event()
def blocked(_target):
    started.set()
    finish.wait(5)
    u.state = "idle"

with patch.object(u, "_deploy", blocked):
    assert u.deploy()
    assert started.wait(2)
    assert not u.deploy()
    try:
        u.select_root(str(base))
        raise AssertionError("任务运行期间不能改安装根目录")
    except ValueError:
        pass
    finish.set()

# 返回 0 但没装到目标位置时也不能报告成功。
other = E.ExternalSoftware(str(base / "failed-data"))
with patch.object(E, "download_installer", fake_download), patch.object(E, "signature_ok", return_value=True), \
        patch.object(E.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
    other._deploy(str(base / "missing-program"))
assert other.state == "error" and not other.installed

# 模型下载只替换校验成功的文件；失败保留原模型，跨盘落盘在目标目录原子替换。
payload = b"model-fixture"
model = P.manager(str(base), u)
assert P.manager(str(base), u) is model
for url in ("http://zenodo.org/x", "https://zenodo.org.evil.test/x", "https://user@zenodo.org/x"):
    assert not P._allowed(url)

class ModelResponse(Response):
    def geturl(self):
        return P.MODEL_URL

real_replace = os.replace
def local_replace(src, dest):
    assert Path(src).parent == Path(dest).parent, "必须在目标磁盘进行最终替换"
    return real_replace(src, dest)

with patch.object(P, "MODEL_SIZE", len(payload)), patch.object(P, "MODEL_MD5", hashlib.md5(payload).hexdigest()), \
        patch("updater.upstream_proxy", return_value=None), patch("urllib.request.build_opener") as op:
    op.return_value.open.return_value = ModelResponse(payload)
    with patch.object(P.os, "replace", local_replace):
        model._download()
    assert model.state == "ready" and model.ready
    assert Path(model.path).read_bytes() == payload
    with patch.object(model, "_md5", side_effect=AssertionError("轮询不能重复计算哈希")):
        assert model.ready
    op.return_value.open.return_value = ModelResponse(b"broken")
    model._download()
    assert model.state == "error" and Path(model.path).read_bytes() == payload
    assert not Path(model.path + ".part").exists()

started.clear()
finish.clear()
def blocked_model():
    started.set()
    finish.wait(5)
    model.external.model_busy = False
    model.state = "idle"

with patch.object(model, "_download", blocked_model):
    assert model.download() and started.wait(2)
    assert not model.download()
    assert not u.deploy()
    assert not u.deploy()
    original_path = model.path
    model.refresh()
    assert model.path == original_path
    try:
        u.select_root(str(base))
        raise AssertionError("模型下载期间不能改安装根目录")
    except ValueError:
        pass
    finish.set()

# 基本设置目录入口与额外功能部署入口可用；任务状态即时更新；切换语言后仍可使用。
root = tk.Tk()
root.geometry("700x650+5000+5000")
sm = SettingsManager(str(base / "settings.json"))
comm = Comm(str(base / "comm-data"), str(base / "personas"))
def label_texts(widget):
    result = [str(widget.cget("text"))] if isinstance(widget, (tk.Label, ttk.Label)) else []
    for child in widget.winfo_children():
        result.extend(label_texts(child))
    return result

try:
    for lang in ("zh", "en"):
        i18n.set_language(lang)
        page = SettingsPage(root, sm, data_dir=str(base / "ui-data"), comm=comm)
        page.pack(fill="both", expand=True)
        root.update()
        assert page.external_deploy.winfo_exists()
        assert "Process Lasso" not in label_texts(page.areas["basic"])
        page.show_tab("extra")
        root.update()
        assert "Process Lasso" in label_texts(page.areas["extra"])
        assert page.external_deploy.winfo_ismapped()
        assert i18n.tr("在部署本地模型时，建议使用该软件。") in label_texts(page.areas["extra"])
        assert i18n.tr("🔊  AI 语音") in label_texts(page.areas["extra"])
        assert i18n.tr("🔊  AI 语音") not in label_texts(page.areas["comm"])
        assert page.piano_model_button.winfo_ismapped()
        assert page.piano_model is P.manager(str(base / "ui-data"), page.external)
        assert not page.external_choose.instate(["disabled"])
        page.external.state = "downloading"
        page._refresh_external_card()
        assert page.external_deploy.instate(["disabled"])
        assert page.external_choose.instate(["disabled"])
        page.external.state = "idle"
        page.destroy()
        root.update()
finally:
    root.destroy()
    i18n.set_language("zh")

assert "chuanapi" not in Path("pages/voice_dialogs.py").read_text(encoding="utf-8")
assert "chuanapi" not in Path("Android version/app/src/main/java/com/obliphur/oit/ui/VoiceDialogs.kt").read_text(encoding="utf-8")
print("external software check OK")
