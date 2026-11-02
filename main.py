

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
        try:      # 不要悄悄退出 (看起来像"打不开"): 说明是因为已经有一个 OIT 在运行 (可能是开发版本 / 另一份安装)
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("OIT", "已经有一个 OIT 在运行 (可能是在后台 / 另一份安装 / 开发版本), 这一个不再打开.\n"
                                       "An OIT is already running (maybe in the background, another install, or a dev checkout); this one will not start.", parent=r)
            r.destroy()
        except Exception:
            pass
        sys.exit(0)

    # 数据的连续性 (MECHANICS 9.3): 数据被搬走 / 倒回去了 (备份还原, 复制到别的电脑 ……) 就清掉, 弹出 "<用户名>,覆水难收."
    import config
    import continuity
    import securefile
    import closedtest
    # 开发版本 (git 仓库) 不加密不核对, 方便调试; 封闭测试 (沙盒, 锚点 / 日志也在沙盒里) 照常加密和核对, 可以在里面试"数据被搬走 / 倒回去会被清掉" (MECHANICS 3.6)
    secure = not updater.is_dev_checkout() or closedtest.active()
    if secure:
        # 运行标记 (MECHANICS 9.5): 上一次没有正常结束 (断电 / 崩溃 / 被结束) 的话, 这一次对"写了一半的文件"宽容, 不当作篡改
        continuity.begin_session(config.app_data_dir())
        securefile.configure(config.app_data_dir())
    elif os.path.exists(os.path.join(config.app_data_dir(), securefile.KEYFILE)):
        # 开发版本和装好的版本用的是同一个数据文件夹: 装好的版本已经把 .json 加密了, 开发版本也要用同一个密钥读写
        # (不然读出一堆乱码当成空数据, 存回去就把数据覆盖了, 对方又会把写进来的明文当成篡改) (MECHANICS 9.4)
        securefile.configure(config.app_data_dir())
    cont, who = continuity.guard(config.app_data_dir(), enabled=secure)
    if secure:
        # 数据和设置的加密 (MECHANICS 9.4): 清过数据 / 第一次运行时重新读密钥; 没有密钥就生成, 现有的明文 .json 转成加密的
        securefile.configure(config.app_data_dir(), on_tamper=lambda name, why: print(f"TAMPER {name}: {why}", file=sys.stderr))
        securefile.ensure_key()
        del securefile.tampered[:]                   # 启动阶段 (清数据 / 迁移) 留下的不算
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
    if secure:
        continuity.end_session(config.app_data_dir())      # 正常退出: 删掉运行标记
    if getattr(app, "restart_requested", False):   # 重置 / 更新后: 以全新状态重新打开
        if relaunch():
            # 新的程序已经打开: 旧进程马上退出. 不等还在跑的后台线程 (过滤代理, 同步 ……),
            # 否则旧进程可能一直留在后台, 占着端口和系统代理
            os._exit(0)


if __name__ == "__main__":
    main()
