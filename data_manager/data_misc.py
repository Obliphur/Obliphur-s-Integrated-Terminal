"""用户名、日常标记、违规记录、重置时删掉全部数据。

从 data_manager.py 拆出来的 mixin; 依赖方向: data_manager.py 导入它们, 它们从不导入 data_manager.py.
"""

import os
import shutil
from datetime import datetime
import config

from data_manager.data_common import (
    VIOLATION_KEEP,
    USERNAME_MAX,
)


class MiscMixin:
    # ---- 用户名 ----

    @property
    def username(self) -> str:
        d = config.load_json_file(self.profile_file, {})
        return str(d.get("username") or "").strip() if isinstance(d, dict) else ""

    def set_username(self, name: str):
        if not self.resetting:
            config.save_json_file(self.profile_file, {"username": str(name).strip()[:USERNAME_MAX]})

    # ---- 日常 (MECHANICS 19): 各种"每天只弹一次"的标记 ----

    def load_daily(self):
        d = config.load_json_file(self.daily_file, {})
        self.daily = {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}

    def daily_flag(self, key: str) -> str:
        """这个标记上次是给哪天 (日期 / 睡觉窗口的那天) 用的; 没弹过返回 ""."""
        return str(self.daily.get(key) or "")

    def set_daily_flag(self, key: str, day: str):
        self.daily[key] = str(day)
        if not self.resetting:
            config.save_json_file(self.daily_file, self.daily)

    # ---- 违规记录 (MECHANICS 8.1): 最近 15 条合约违规的提示文字 ----

    def load_violations(self):
        d = config.load_json_file(self.violations_file, [])
        self.violations = ([x for x in d if isinstance(x, dict) and str(x.get("text") or "").strip()]
                           if isinstance(d, list) else [])[-VIOLATION_KEEP:]

    def add_violation(self, text: str):
        """记一条违规 (text = 弹出的那段提示); 只留最近 15 条."""
        text = str(text or "").strip()
        if not text:
            return
        self.violations.append({"at": f"{datetime.now():%Y-%m-%d %H:%M}", "text": text})
        self.violations = self.violations[-VIOLATION_KEEP:]
        if not self.resetting:
            config.save_json_file(self.violations_file, self.violations)

    def recent_violations(self, n: int = VIOLATION_KEEP) -> list:
        """最近的违规: 新的在前."""
        return list(self.violations[-max(0, int(n)):])[::-1]

    def erase_all_data(self):
        """重置: 删除本程序留下的全部数据, 不留任何记录."""
        try:
            import recorder
            for kind in recorder.KINDS:
                shutil.rmtree(recorder.work_dir(self.data_dir, kind), ignore_errors=True)
            shutil.rmtree(recorder.play_dir(), ignore_errors=True)
        except Exception:
            pass
        try:
            import tempstore
            tempstore.sweep(0)          # 临时文件夹 (%TEMP%\OIT) 里的也清掉: 录了一半的 mp4 / 解密的回放 / 解包目录
        except Exception:
            pass
        shutil.rmtree(self.data_dir, ignore_errors=True)
        shutil.rmtree(self.old_dir, ignore_errors=True)
        try:
            os.remove(self.legacy_uninstall_file)
        except OSError:
            pass
