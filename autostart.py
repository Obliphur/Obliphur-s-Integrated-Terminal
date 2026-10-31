"""
开机自启动 (MECHANICS.md 3.4)
──────────────────────────────────────────────────────
OIT 要以管理员身份运行, 放进"启动"文件夹 / 注册表 Run 键的话每次登录都会弹 UAC.
所以用 Windows 任务计划程序建一个任务, 登录时以最高权限启动:
    OIT autostart      当前用户登录时运行 pythonw.exe main.py (打包成 exe 时直接运行 exe)
默认开启: 程序第一次运行时建好 (数据目录里记下 autostart_created, 之后就不再自己建).
用户在任务计划程序里禁用 / 删掉了这个任务就算关掉了开机自启动, OIT 不会再把它打开 (重置后算第一次, 会再建).
任务还开着时, 每次启动都按当前的程序位置重新写一遍 (程序文件夹搬过家也能启动). 卸载时删掉 (uninstall.py).

开机自启动开着, 是其他设备把本机算作"关机"而不是"离线"的前提 (MECHANICS 15.4).

    python autostart.py      查看任务在不在 / 开没开 (只读)
"""

import os
import subprocess
import sys
import tempfile

NO_WINDOW = 0x08000000
TASK_NAME = "OIT autostart"
LINK_TASK = "OIT link"          # 联动程序 (linkd.py, MECHANICS 15.8): 登录时启动, OIT 主体关掉了它照样在
MARKER = "autostart_created"


def _run(args, timeout: float = 30):
    """运行一条命令, 返回 (退出码, 输出). 非 Windows 什么都不做."""
    if sys.platform != "win32":
        return 0, ""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        out = (r.stdout or b"") + (r.stderr or b"")
        try:
            return r.returncode, out.decode("utf-8").strip()
        except UnicodeDecodeError:
            return r.returncode, out.decode("mbcs", "replace").strip()     # 系统语言的代码页 (中文: GBK)
    except Exception as e:
        return -1, str(e)


def _xml_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def task_xml(command: str, arguments: str, workdir: str, user: str, desc: str = "OIT: start at logon (MECHANICS 3.4)") -> str:
    """任务的定义: 这个用户登录时以最高权限启动; 用电池也照常运行, 不限时长, 已经在运行就不再开一个."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{_xml_escape(desc)}</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{_xml_escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{_xml_escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>false</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{_xml_escape(command)}</Command><Arguments>{_xml_escape(arguments)}</Arguments><WorkingDirectory>{_xml_escape(workdir)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def _user() -> str:
    dom, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{dom}\\{name}" if dom else name


def state(run=_run, name: str = TASK_NAME) -> str:
    """"on" 任务在而且开着 / "off" 任务在但被禁用了 / "missing" 没有这个任务 / "error" 查不了."""
    if sys.platform != "win32":
        return "missing"
    rc, text = run(["schtasks", "/Query", "/TN", name, "/XML"])
    if rc != 0:
        return "missing" if rc == 1 else "error"
    # 任务的 <Settings> 里 <Enabled>false</Enabled> = 被禁用了 (XML 不随系统语言变)
    settings = text.split("<Settings>", 1)[-1].split("</Settings>", 1)[0]
    return "off" if "<Enabled>false</Enabled>" in settings.replace(" ", "") else "on"


def create(run=_run) -> bool:
    """建好 / 重写任务 (指向当前的程序位置). 需要管理员权限."""
    if sys.platform != "win32":
        return False
    from shortcut import launch_target
    exe, args, workdir = launch_target()
    xml = task_xml(exe, args, workdir, _user())
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(xml.encode("utf-16"))
        rc, _ = run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
        return rc == 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def ensure(data_dir: str, run=_run) -> str:
    """程序启动时 (后台线程): 第一次运行时建好任务 (默认开启); 之后任务开着就按当前位置重写一遍,
    被用户禁用 / 删掉了就不动. 返回之后的状态 (见 state)."""
    if sys.platform != "win32":
        return "missing"
    marker = os.path.join(data_dir, MARKER)
    st = state(run)
    if not os.path.exists(marker):
        if create(run):
            try:
                os.makedirs(data_dir, exist_ok=True)
                with open(marker, "w", encoding="utf-8") as f:
                    f.write("1")
            except OSError:
                pass
        return state(run)
    if st == "on":
        create(run)
    return st


def link_state(run=_run) -> str:
    """联动程序的任务: "on" / "off" / "missing" / "error" (同 state)."""
    return state(run, LINK_TASK)


def link_ensure(run=_run) -> str:
    """OIT 开机自启动开着 (state() == "on") 时, 联动程序的任务也要在: 没有就建, 有就按当前位置重写. 返回之后的状态.
    OIT 的开机自启动被用户关掉 / 删掉时不建 (已有的任务也不动, 被禁用了就算关掉)."""
    if sys.platform != "win32" or state(run) != "on":
        return link_state(run)
    st = link_state(run)
    if st in ("missing", "on"):
        import linkd
        exe, script, workdir = linkd.link_command()
        xml = task_xml(exe, f'"{script}"', workdir, _user(), desc="OIT link: companion program (MECHANICS 15.8)")
        fd, path = tempfile.mkstemp(suffix=".xml")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(xml.encode("utf-16"))
            run(["schtasks", "/Create", "/TN", LINK_TASK, "/XML", path, "/F"])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        st = link_state(run)
    return st


def link_remove(run=_run) -> bool:
    """卸载时: 删掉联动程序的任务."""
    if sys.platform != "win32" or state(run, LINK_TASK) == "missing":
        return True
    return run(["schtasks", "/Delete", "/TN", LINK_TASK, "/F"])[0] == 0


def remove(run=_run) -> bool:
    """卸载时: 删掉任务. 返回删干净了没有 (本来就没有也算)."""
    if sys.platform != "win32" or state(run) == "missing":
        return True
    return run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])[0] == 0


def _selftest():
    import shutil
    calls = []
    tasks = {}

    def fake(args, timeout=30):
        calls.append(args)
        name = args[args.index("/TN") + 1]
        if args[1] == "/Query":
            if name not in tasks:
                return 1, "ERROR: not found"
            return 0, tasks[name]
        if args[1] == "/Create":
            with open(args[5], "rb") as f:
                tasks[name] = f.read().decode("utf-16")
            return 0, "SUCCESS"
        if args[1] == "/Delete":
            tasks.pop(name, None)
            return 0, "SUCCESS"
        return 1, ""

    def _set_enabled(xml_text, on):
        a, b = ("false", "true") if on else ("true", "false")
        nl = chr(10) + "    <Hidden>"
        return xml_text.replace("<Enabled>" + a + "</Enabled>" + nl, "<Enabled>" + b + "</Enabled>" + nl)

    xml = task_xml("C:\\py\\pythonw.exe", '"C:\\a&b\\main.py"', "C:\\a&b", "PC\\me")
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml and "C:\\a&amp;b" in xml and "&quot;" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    if sys.platform != "win32":
        return "autostart selftest OK (non-Windows)"
    d = tempfile.mkdtemp()
    try:
        assert state(fake) == "missing"
        assert ensure(d, fake) == "on" and os.path.exists(os.path.join(d, MARKER)), "第一次运行: 默认开启"
        # 用户在任务计划程序里禁用了: 不再打开
        tasks[TASK_NAME] = tasks[TASK_NAME].replace("<Enabled>true</Enabled>\n    <Hidden>",
                                                    "<Enabled>false</Enabled>\n    <Hidden>")
        n = len(calls)
        assert ensure(d, fake) == "off" and not any(c[1] == "/Create" for c in calls[n:])
        # 用户删掉了: 也不再建
        tasks.clear()
        assert ensure(d, fake) == "missing" and TASK_NAME not in tasks
        # 重置 (数据目录清空) 后算第一次
        os.remove(os.path.join(d, MARKER))
        assert ensure(d, fake) == "on"
        # 联动程序的任务 (15.8): OIT 的开机自启动开着才建; 关了就不建
        assert link_state(fake) == "missing" and link_ensure(fake) == "on" and "linkd.py" in tasks[LINK_TASK]
        n = len(calls)
        assert link_ensure(fake) == "on" and any(c[1] == "/Create" for c in calls[n:]), "按当前位置重写"
        assert link_remove(fake) and link_state(fake) == "missing" and link_remove(fake)
        tasks[TASK_NAME] = _set_enabled(tasks[TASK_NAME], False)
        assert link_ensure(fake) == "missing" and LINK_TASK not in tasks, "OIT 的开机自启动关了: 不建"
        tasks[TASK_NAME] = _set_enabled(tasks[TASK_NAME], True)
        assert remove(fake) and state(fake) == "missing" and remove(fake)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "autostart selftest OK"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        print(_selftest())
    else:
        print(f"{TASK_NAME}: {state()}")
