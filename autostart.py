"""
开机自启动 (MECHANICS.md 3.4)
──────────────────────────────────────────────────────
OIT 要以管理员身份运行, 放进"启动"文件夹 / 注册表 Run 键的话每次登录都会弹 UAC.
所以用 Windows 任务计划程序建一个任务, 登录时以最高权限启动:
    OIT autostart      当前用户登录时运行 pythonw.exe main.py (打包成 exe 时直接运行 exe)
默认开启: 程序第一次运行时建好 (数据目录里记下 autostart_created).
任务被禁用 / 被删掉了就重建 (启动时, 之后每 5 分钟看一次; 联动程序也一样, 见 link_ensure): 用户不能靠在任务计划程序里禁用 / 删掉它来关掉开机自启动.
任务在时, 每次也都按当前的程序位置重新写一遍 (程序文件夹搬过家也能启动). 卸载时删掉 (uninstall.py).

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


def task_xml(command: str, arguments: str, workdir: str, user: str, desc: str = "OIT: start at logon (MECHANICS 3.4)",
             repeat: str = "") -> str:
    """任务的定义: 这个用户登录时以最高权限启动; 用电池也照常运行, 不限时长, 已经在运行就不再开一个.
    repeat (如 "PT1M"): 登录之后还按这个间隔反复触发 (进程被结束了也会回来; 在运行就不会再开, IgnoreNew)."""
    again = ("\n    <TimeTrigger><Repetition><Interval>" + repeat + "</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"
             "<StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>") if repeat else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{_xml_escape(desc)}</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{_xml_escape(user)}</UserId></LogonTrigger>{again}
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


def _register(name: str, xml: str, run=_run) -> bool:
    """用 xml 建 / 覆盖任务 name (/F: 被禁用的任务也被覆盖, 新的定义是开着的)."""
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(xml.encode("utf-16"))
        return run(["schtasks", "/Create", "/TN", name, "/XML", path, "/F"])[0] == 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def create(run=_run) -> bool:
    """建好 / 重写任务 (指向当前的程序位置, 开着). 需要管理员权限."""
    if sys.platform != "win32":
        return False
    from shortcut import launch_target
    exe, args, workdir = launch_target()
    return _register(TASK_NAME, task_xml(exe, args, workdir, _user()), run)


def ensure(data_dir: str, run=_run) -> str:
    """程序启动时 (后台线程) 和之后每 5 分钟: 任务没有 / 被禁用了就重建, 开着的也按当前位置重写一遍. 返回之后的状态 (见 state).
    查不了任务 (error) 时什么都不动."""
    if sys.platform != "win32":
        return "missing"
    if state(run) == "error":
        return "error"
    if create(run):
        marker = os.path.join(data_dir, MARKER)
        if not os.path.exists(marker):
            try:
                os.makedirs(data_dir, exist_ok=True)
                with open(marker, "w", encoding="utf-8") as f:
                    f.write("1")
            except OSError:
                pass
    return state(run)


def link_state(run=_run) -> str:
    """联动程序的任务: "on" / "off" / "missing" / "error" (同 state)."""
    return state(run, LINK_TASK)


def link_ensure(run=_run) -> str:
    """联动程序的任务也要在而且开着: 没有 / 被禁用了就重建, 有就按当前位置重写. OIT 的开机自启动被禁用 / 删掉时,
    联动程序 (和 OIT 一样) 把它也重建. 返回联动程序任务之后的状态."""
    if sys.platform != "win32":
        return link_state(run)
    if state(run) in ("off", "missing"):
        try:
            create(run)
        except Exception:
            pass
    if link_state(run) != "error":
        import linkd
        exe, script, workdir = linkd.link_command()
        _register(LINK_TASK, task_xml(exe, f'"{script}"', workdir, _user(), desc="OIT link: companion program (MECHANICS 15.8)", repeat="PT1M"), run)
    return link_state(run)


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
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml and "TimeTrigger" not in xml
    rx = task_xml(r"C:\py\oitlink.exe", r'"C:\a\linkd.py"', r"C:\d", r"PC\me", repeat="PT1M")
    assert "<Interval>PT1M</Interval>" in rx and rx.index("LogonTrigger") < rx.index("TimeTrigger") < rx.index("</Triggers>")
    if sys.platform != "win32":
        return "autostart selftest OK (non-Windows)"
    d = tempfile.mkdtemp()
    try:
        assert state(fake) == "missing"
        assert ensure(d, fake) == "on" and os.path.exists(os.path.join(d, MARKER)), "第一次运行: 默认开启"
        # 用户在任务计划程序里禁用了: 重建 (重新开着)
        tasks[TASK_NAME] = _set_enabled(tasks[TASK_NAME], False)
        assert state(fake) == "off"
        assert ensure(d, fake) == "on" and state(fake) == "on", "被禁用: 重建"
        # 用户删掉了: 也重建
        tasks.clear()
        assert ensure(d, fake) == "on" and TASK_NAME in tasks, "被删掉: 重建"
        # 查不了任务 (error): 不动
        n = len(calls)
        assert ensure(d, lambda a, timeout=30: (5, "denied")) == "error"
        # 联动程序的任务 (15.8)
        assert link_state(fake) == "missing" and link_ensure(fake) == "on" and "linkd.py" in tasks[LINK_TASK] and "<Interval>PT1M</Interval>" in tasks[LINK_TASK]
        n = len(calls)
        assert link_ensure(fake) == "on" and any(c[1] == "/Create" for c in calls[n:]), "按当前位置重写"
        tasks[LINK_TASK] = _set_enabled(tasks[LINK_TASK], False)
        assert link_state(fake) == "off" and link_ensure(fake) == "on", "联动程序的任务被禁用: 重建"
        assert link_remove(fake) and link_state(fake) == "missing" and link_remove(fake)
        assert link_ensure(fake) == "on" and LINK_TASK in tasks, "联动程序的任务被删掉: 重建"
        # OIT 的开机自启动被禁用 / 删掉时, 联动程序把它也重建
        tasks[TASK_NAME] = _set_enabled(tasks[TASK_NAME], False)
        assert link_ensure(fake) == "on" and state(fake) == "on", "联动程序也重建 OIT 的开机自启动 (禁用)"
        tasks.pop(TASK_NAME)
        assert link_ensure(fake) == "on" and state(fake) == "on", "联动程序也重建 OIT 的开机自启动 (删掉)"
        assert remove(fake) and state(fake) == "missing" and remove(fake)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "autostart selftest OK"


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        print(_selftest())
    else:
        print(f"{TASK_NAME}: {state()}")
