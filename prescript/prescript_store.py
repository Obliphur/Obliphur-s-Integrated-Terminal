"""指令存档与当天状态: prescript.json 的读写、换日、每天的随机次数。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime

import config

from prescript.prescript_common import (
    FOCUS_CHANCE_START, SAVE_EVERY,
)


class StoreMixin:
    def _load(self) -> dict:
        raw = config.load_json_file(self.path, {})
        data = raw if isinstance(raw, dict) else {}
        base = {
            "current": None,
            "extra": [],
            "last_pool": "",
            "last_type": "",
            "run": [],                  # 连续的空闲池抽取里已经抽到过的指令 (空闲5min 除外)
            "force_focus": False,       # 下一次抽取改抽专注池
            "focus_chance": FOCUS_CHANCE_START,
            "closes": [],               # OIT 被手动关闭的时刻
            "logs": {},                 # X 词条保存的记录 {日期: 文字}
            "pending": [],
            "settled": {},              # 已经结算过的奖励 {id: 奖励} (同步给其他设备, 免得重复结算)
            "granted": [],
            "daily": {},
            "history": [],
        }
        base.update(data)
        for key in ("pending", "granted", "history", "run", "closes"):
            if not isinstance(base.get(key), list):
                base[key] = []
        for key in ("daily", "logs", "settled"):
            if not isinstance(base.get(key), dict):
                base[key] = {}
        try:
            base["focus_chance"] = min(1.0, max(0.0, float(base.get("focus_chance"))))
        except (TypeError, ValueError):
            base["focus_chance"] = FOCUS_CHANCE_START
        if base.get("current") is not None and not isinstance(base.get("current"), dict):
            base["current"] = None
        extra = base.get("extra")
        base["extra"] = [e for e in extra if isinstance(e, dict)] if isinstance(extra, list) else []
        return base

    def save(self):
        if self.blocked_fn():
            return
        config.save_json_file(self.path, self._data)
        self._last_save = datetime.now()
        self._dirty = False

    def _save_if_due(self, now: datetime):
        if self._dirty and (now - self._last_save).total_seconds() >= SAVE_EVERY:
            self.save()

    def _changed(self):
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            pass

    # ---- 当天 ----

    def _ensure_day(self, now: datetime) -> dict:
        daily = self._data.setdefault("daily", {})
        day = now.strftime("%Y-%m-%d")
        if daily.get("date") == day:
            return daily
        for cur in list(self.actives()):
            if cur.get("type") == "IV":
                # 这一天结束了: 还没完成就作废.
                self._finish("expired", datetime.combine(now.date(), datetime.min.time()),
                             save=False, notify=False, settle=False, cur=cur)
        self._data["run"] = []
        daily.clear()
        daily.update({
            "date": day,
            "n_ii": self.rng.randint(1, 20),
            "n_iv": self.rng.randint(5, 8),
            "completed_t": 0.0,
            "bonus": False,
            "iv_done": False,
        })
        self._dirty = True
        return daily

    def _daily(self, now: datetime) -> dict:
        return self._ensure_day(now)
