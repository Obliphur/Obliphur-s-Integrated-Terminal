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

import json as _json
appdata_dir = base / "AppData"
obs_cfg = appdata_dir / "obsidian" / "obsidian.json"
obs_cfg.parent.mkdir(parents=True)
existing_vault = base / "existing-vault"
existing_vault.mkdir()
obs_cfg.write_text(_json.dumps({"vaults": {"1122334455667788": {"path": str(existing_vault), "ts": 1000, "open": True}}}), encoding="utf-8")
kb_vault = base / "knowledge_vault"
(kb_vault / ".oit_kb").mkdir(parents=True)
card_md = kb_vault / "技巧性" / "数学" / "示例.md"
card_md.parent.mkdir(parents=True)
card_md.write_text("# test\n", encoding="utf-8")
launched = []
def fake_popen(args, **kwargs):
    launched.append(args)

with patch.dict(os.environ, {"APPDATA": str(appdata_dir)}, clear=False), \
        patch.object(E.subprocess, "Popen", side_effect=fake_popen):
    # 未注册且 Obsidian 运行中：不修改 obsidian.json，不启动，报明确提示
    with patch.object(E, "_obsidian_running", return_value=True):
        try:
            u.open_obsidian(kb_vault)
            raise AssertionError("Obsidian 运行中打开未注册库应提示关闭或手动添加")
        except ValueError as err:
            assert str(kb_vault.resolve()) in str(err)
    assert not launched
    assert len(_json.loads(obs_cfg.read_text(encoding="utf-8"))["vaults"]) == 1

    # 未注册且 Obsidian 未运行：自动注册到 obsidian.json 并以 vault ID 启动
    with patch.object(E, "_obsidian_running", return_value=False):
        u.open_obsidian(kb_vault)
    cfg_after = _json.loads(obs_cfg.read_text(encoding="utf-8"))
    assert "1122334455667788" in cfg_after["vaults"]
    new_ids = [k for k in cfg_after["vaults"] if k != "1122334455667788"]
    assert len(new_ids) == 1
    new_id = new_ids[0]
    assert Path(cfg_after["vaults"][new_id]["path"]).resolve() == kb_vault.resolve()
    assert launched[-1] == [str(obsidian_dir / "Obsidian.exe"), f"obsidian://open?vault={new_id}"]

    # 已注册库内文件（即使 Obsidian 正在运行）：直接用 vault + file URI 打开
    with patch.object(E, "_obsidian_running", return_value=True):
        u.open_obsidian(card_md)
    assert launched[-1][0] == str(obsidian_dir / "Obsidian.exe")
    assert launched[-1][1].startswith(f"obsidian://open?vault={new_id}&file=")
    assert "技巧性" not in launched[-1][1]  # 已 URL 编码

    # path=None 直接打开
    u.open_obsidian(None)
    assert launched[-1] == [str(obsidian_dir / "Obsidian.exe"), "obsidian://open"]

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
