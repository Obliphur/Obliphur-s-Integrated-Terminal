"""联网状态的存档: net_state.json / net_usage.json 的读写, 联网会话的记录与日志。

从 net_guard.py 拆出来的 mixin; 依赖方向: net_guard.py 导入它们, 它们从不导入 net_guard.py.
"""

import os
import json
import time
from datetime import datetime
from data_manager import month_prefixes
import securefile


class StoreMixin:
    def _load(self):
        try:
            with securefile.open(self.state_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            return
        if not isinstance(d, dict):
            return
        saved = str(d.get("date") or "")

        def num(key):
            try:
                return max(0.0, float(d.get(key, 0.0)))
            except (TypeError, ValueError):
                return 0.0

        if isinstance(d.get("live"), dict):
            self._saved_live = d["live"]          # __init__ 最后照着恢复
        if saved == self.date:
            self.used = num("used")
            self.bonus = num("bonus")
            self.gift = num("gift")
            self.tomorrow_used = num("tomorrow_used")
            self.debt = num("debt")
        elif saved and saved < self.date:
            # 存档是之前某一天的: 程序关着跨过了零点.
            # 借贷要还的部分照样变成今天的欠款 —— 以前这里什么都不读,
            # 借完额度后关掉程序, 第二天再打开, 欠款就凭空消失了.
            # 中间隔了几天也一样: 欠款记到借贷之后第一次打开程序的那一天.
            self.debt = num("tomorrow_used")
            if self.debt > 0:
                self._save()
                self._log(f"LOAN     repay {int(self.debt // 60)} min today "
                          f"(borrowed on {saved})")

    def _save(self):
        if self.dead:
            return
        try:
            with securefile.open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "date": self.date,
                    "used": int(self.used),
                    "bonus": int(self.bonus),
                    "gift": int(self.gift),
                    "tomorrow_used": int(self.tomorrow_used),
                    "debt": int(self.debt),
                    "live": self.live(),
                }, f)
        except IOError:
            pass
        self._save_usage()
        self._last_save = time.monotonic()

    def _load_usage(self):
        try:
            with securefile.open(self.usage_file, 'r', encoding='utf-8') as f:
                d = json.load(f)
            recs = d.get("sessions", []) if isinstance(d, dict) else []
            self.usage = [r for r in recs if isinstance(r, dict) and "date" in r]
        except Exception:
            self.usage = []
        keep = month_prefixes()
        self.usage = [r for r in self.usage if str(r.get("date", ""))[:7] in keep]

    def _save_usage(self):
        if self.dead or not self._usage_dirty:
            return
        keep = month_prefixes()
        self.usage = [r for r in self.usage if str(r.get("date", ""))[:7] in keep]
        if self._session is not None and self._session not in self.usage:
            self._session = None
        try:
            tmp = self.usage_file + ".tmp"
            with securefile.open(tmp, 'w', encoding='utf-8') as f:
                json.dump({"sessions": self.usage}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.usage_file)
            self._usage_dirty = False
        except (IOError, OSError):
            pass

    def _open_session(self, reason: str, quick: bool, minutes: int, start: datetime = None):
        start = start or datetime.now()
        self._session = {
            "date": start.strftime('%Y-%m-%d'),
            "start": start.strftime('%H:%M'),
            "reason": reason,
            "quick": bool(quick),
            "minutes": int(minutes),
            "seconds": 0,
        }
        self.usage.append(self._session)
        self._usage_dirty = True

    def _count_online(self, dt: float):
        """实际联网的秒数(不乘快速访问倍数),跨零点自动拆到新的一天."""
        if dt <= 0 or self._session is None:
            return
        now = datetime.now()
        if self._session["date"] != now.strftime('%Y-%m-%d'):
            s = self._session
            self._open_session(s["reason"], s["quick"], s["minutes"],
                               start=now.replace(hour=0, minute=0, second=0, microsecond=0))
        self._session["seconds"] = round(self._session["seconds"] + dt, 1)
        self._usage_dirty = True

    def usage_by_reason(self, prefix: str):
        """某个月 [(理由, 实际联网秒数)]."""
        peer = getattr(self, "peer_usage", None)
        recs = list(self.usage) + list((peer() if peer else None) or [])
        return [(r.get("reason", ""), float(r.get("seconds", 0)))
                for r in recs if isinstance(r, dict) and str(r.get("date", "")).startswith(prefix)]

    def _log(self, text: str):
        if self.dead:
            return
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
        except IOError:
            pass
