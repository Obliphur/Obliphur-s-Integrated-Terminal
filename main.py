

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

    # 数据的连续性 (MECHANICS 9.3): 数据被搬走 / 倒回去了 (备份还原, 复制到别的电脑 ……) 就清掉, 弹出 "<用户名>,覆水难收."
    import config
    import continuity
    import securefile
    secure = not updater.is_dev_checkout()           # 开发版本 (git 仓库) 不加密不核对, 方便调试
    if secure:
        securefile.configure(config.app_data_dir())
    cont, who = continuity.guard(config.app_data_dir(), enabled=secure)
    if secure:
        # 数据和设置的加密 (MECHANICS 9.4): 清过数据 / 第一次运行时重新读密钥; 没有密钥就生成, 现有的明文 .json 转成加密的
        securefile.configure(config.app_data_dir(), on_tamper=lambda name, why: print(f"TAMPER {name}: {why}", file=sys.stderr))
        securefile.ensure_key()
        cont.tick()                                  # 连续性记录里带上密钥的指纹
    if who is not None:
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showwarning("OIT", continuity.message(who), parent=r)
        r.destroy()
    if sys.platform == "win32":
        try:      # 任务栏上单独成组, 用 OIT 自己的图标 (不然和别的 Python 程序混在一起, 显示 Python 的图标)
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Obliphur.OIT")
        except Exception:
            pass
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
