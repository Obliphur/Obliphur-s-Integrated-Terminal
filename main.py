

import os
import sys
import tkinter as tk

from config import is_admin, elevate, relaunch
from app import BreakTimerApp


def main():
    if sys.platform == "win32" and not is_admin():
        elevate()
        sys.exit(0)

    # 单实例 (MECHANICS 3.5): 已经有 OIT 在运行就退出 (更新 / 重置后的重新打开最多等旧进程放手 15 秒); git 仓库里的开发版本不受限
    import instance
    import updater
    if not instance.acquire(enforce=not updater.is_dev_checkout()):
        sys.exit(0)

    root = tk.Tk()
    app = BreakTimerApp(root)
    root.mainloop()
    if getattr(app, "restart_requested", False):   # 重置 / 更新后: 以全新状态重新打开
        if relaunch():
            # 新的程序已经打开: 旧进程马上退出. 不等还在跑的后台线程 (过滤代理, 同步 ……),
            # 否则旧进程可能一直留在后台, 占着端口和系统代理
            os._exit(0)


if __name__ == "__main__":
    main()
