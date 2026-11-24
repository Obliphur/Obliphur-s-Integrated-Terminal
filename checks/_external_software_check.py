"""外部软件部署：只用临时目录和安装器替身，不安装真实软件。"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempstore
tempstore.session("external_software_check")

import configparser
import fnmatch
from pathlib import Path
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

import config
import external_software as E
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

obsidian_dir = base / "Obsidian"
obsidian_dir.mkdir()
(obsidian_dir / "Obsidian.exe").write_bytes(b"MZ")
try:
    u.select_obsidian_dir("relative")
    raise AssertionError("Obsidian 安装目录必须是包含 Obsidian.exe 的绝对路径")
except ValueError:
    pass
u.select_obsidian_dir(str(obsidian_dir))
assert u.obsidian_dir == str(obsidian_dir)
local_default = base / "LocalAppData" / "Programs" / "Obsidian"
local_default.mkdir(parents=True)
(local_default / "Obsidian.exe").write_bytes(b"MZ")
auto_obsidian = E.ExternalSoftware(str(base / "auto-data"))
with patch.dict(os.environ, {"LOCALAPPDATA": str(base / "LocalAppData")}, clear=False):
    assert auto_obsidian.obsidian_dir == str(local_default)

for url in ("http://bitsum.com/x", "https://bitsum.com.evil.test/x", "https://evil.test/x", "https://user@bitsum.com/x"):
    assert not E._official_url(url)
    try:
        import updater
        updater.download_file({"url": url}, str(base / "denied.exe"), allowed_hosts=E.HOSTS,
                              max_download=config.EXTERNAL_DOWNLOAD_MAX)
        raise AssertionError("不能重定向到非官方地址")
    except ValueError:
        pass

download = base / "installer.exe"
def fake_unified_download(source, destination, **kwargs):
    assert source["url"].startswith("https://bitsum.com/")
    assert kwargs["allowed_hosts"] == E.HOSTS and kwargs["max_download"] == config.EXTERNAL_DOWNLOAD_MAX
    Path(destination).write_bytes(fake_download_content[0])

for content in (b"MZ" + b"x" * 110000, b"<html>" * 20000):
    fake_download_content = [content]
    with patch("updater.download_file", side_effect=fake_unified_download) as download_call:
        try:
            E.download_installer(str(download))
            assert content.startswith(b"MZ") and download_call.call_count == 1
        except ValueError:
            assert not content.startswith(b"MZ")

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
for name in ("python.exe", "pythonw.exe", "python3.13.exe", "torchrun.exe", "oit.exe", "oitlink.exe",
             "java.exe", "javaw.exe", "Audiveris.exe", "MuseScore4.exe", "mscore.exe"):
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
        assert page.music_install.winfo_ismapped()
        assert page.java_install.winfo_ismapped()
        assert i18n.tr("Android 构建 Java") in label_texts(page.areas["extra"])
        assert i18n.tr("音乐工具") in label_texts(page.areas["extra"])
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
