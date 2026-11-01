"""
封闭测试 (MECHANICS.md 3.6): 开发环境里的沙盒
──────────────────────────────────────────────
开发版本 (git 仓库里, 程序文件夹有 .git) 默认用的是真正的数据 (AppData\\LocalLow\\<程序名>), 测试时改坏 / 清掉数据就是改坏了主设备上的数据.
封闭测试打开之后, 这个实例是一个和主设备完全隔开的沙盒:
  · 数据全部放在项目根目录的 `closed_test\\` 里 (数据文件夹 `closed_test\\<程序名>`, 旧版本目录和卸载次数文件也在 `closed_test\\`), 不碰 AppData 里的数据;
  · 设备同步整个关掉 (不监听端口, 不广播, 不绑定, 也就不会把测试数据同步给主设备); 不放行防火墙, 不动合约的防火墙规则;
  · 不建 / 不改任务计划程序里的开机自启动和联动程序, 也不启动联动程序;
  · 开发者模式自动打开 (不管 DEV_AUTO): 开发者模式下 OIT 不接管系统代理 / 网卡, 关掉程序也不断网;
  · 窗口标题带 [封闭测试], 打开时提示数据放在哪里.
只在开发版本里有效 (公开发布版里 active() 永远是假, 参数 / 环境变量 / 本地配置都没用). 打开的办法 (任选一个):
    python main.py --closed-test        命令行参数 (closed_test.bat 就是这样打开的)
    set OIT_CLOSED_TEST=1               环境变量
    config_local.py 里写 CLOSED_TEST = True
想清掉测试数据: 直接删 `closed_test\\` 文件夹 (已加入 .gitignore).

    python closedtest.py       自检
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FLAG = "--closed-test"
ENV = "OIT_CLOSED_TEST"
DIRNAME = "closed_test"
_active = None


def dev_checkout(root: str = HERE) -> bool:
    return os.path.isdir(os.path.join(root, ".git"))


def requested(argv=None, env=None, local=None) -> bool:
    """有没有要求封闭测试 (参数 / 环境变量 / config_local 里的 CLOSED_TEST)."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    if FLAG in argv or str(env.get(ENV, "")).strip().lower() in ("1", "true", "yes", "on"):
        return True
    if local is None:
        try:
            import config_local as local            # noqa: F811
        except Exception:
            return False
    return bool(getattr(local, "CLOSED_TEST", False))


def active() -> bool:
    """封闭测试开着吗 (整个进程里不变)."""
    global _active
    if _active is None:
        _active = dev_checkout() and requested()
    return _active


def low_dir(root: str = HERE) -> str:
    """代替 AppData\\LocalLow 的文件夹."""
    return os.path.join(root, DIRNAME)


def tag() -> str:
    return " [封闭测试]" if active() else ""


def _selftest():
    import shutil
    import tempfile
    import types
    none = types.SimpleNamespace()
    assert not requested([], {}, none)
    assert requested(["main.py", FLAG], {}, none)
    assert requested([], {ENV: "1"}, none) and requested([], {ENV: "TRUE"}, none)
    assert not requested([], {ENV: "0"}, none)
    assert requested([], {}, types.SimpleNamespace(CLOSED_TEST=True))
    d = tempfile.mkdtemp()
    try:
        assert not dev_checkout(d)
        os.makedirs(os.path.join(d, ".git"))
        assert dev_checkout(d) and low_dir(d) == os.path.join(d, DIRNAME)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # 打开封闭测试的进程里: 数据文件夹在项目根目录里, 不在 AppData
    import subprocess
    code = ("import sys; sys.argv.append('--closed-test'); sys.path.insert(0, %r); import config, closedtest, data_manager; "
            "print(closedtest.active(), config.app_data_dir())" % HERE)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=HERE).stdout.split(None, 1)
    if dev_checkout():
        assert out[0] == "True" and out[1].strip().startswith(os.path.join(HERE, DIRNAME)), out
    else:
        assert out[0] == "False", out
    return "closedtest selftest OK"


if __name__ == "__main__":
    print(_selftest())
