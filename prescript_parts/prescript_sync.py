"""设备同步: journal / sync_doc / sync_apply (devsync 的 prescript 文档, MECHANICS 22.9)。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime, timedelta

from prescript_parts.prescript_common import (
    HISTORY_KEEP, PAGE_EVENT_FRESH, SYNC_DAYS, SYNC_VOLATILE, _iso, _parse,
)


class SyncMixin:
    @staticmethod
    def _static(rec: dict) -> dict:
        return {k: v for k, v in rec.items() if k not in SYNC_VOLATILE}

    def journal_view(self) -> str:
        """统计页的日志上一次被看的时刻 ("YYYY-MM-DDTHH:MM:SS", 没看过 = ""); 随同步, 几台设备共用 2 小时的冷却 (MECHANICS 8.7)."""
        return str(self._data.get("journal_view") or "")

    def logs_newest(self) -> list:
        """X 词条保存过的记录 [(日期, 文字)], 新的在前; 统计页的日志里显示."""
        logs = self._data.get("logs")
        if not isinstance(logs, dict):
            return []
        return [(day, str(logs[day])) for day in sorted(logs, reverse=True) if str(logs[day]).strip()]

    def history_brief(self, n: int = 10) -> list:
        """最近结束的几条指令 [(结束时刻, 类型, 结果, 内容)]; 沟通里对方查询"指令"时用."""
        return [(str(r.get("ended") or ""), str(r.get("type") or ""), str(r.get("result") or ""), str(r.get("body") or ""))
                for r in (self._data.get("history") or [])[-n:]]

    def note_journal(self, now: datetime = None):
        now = (now or datetime.now()).replace(microsecond=0)
        self._data["journal_view"] = _iso(now)
        self._dirty = True
        self.save()

    def note_page(self, on: bool, device_id: str, now: datetime = None):
        """本机进入 / 退出了指令页: 记成一个事件, 随同步发给其他设备, 它们跟着进入 / 退出 (调用的人负责马上同步一次)."""
        now = (now or datetime.now()).replace(microsecond=0)
        self._page_rec = {"on": bool(on), "at": _iso(now), "by": str(device_id or "")}

    def _adopt_page(self, val: dict, now: datetime) -> None:
        """别的设备发来的进入 / 退出指令页的事件: 记下 (本机之后不会又发回去), 够新的话让本机跟着做."""
        if self._page_rec == {"on": bool(val.get("on")), "at": str(val.get("at") or ""), "by": str(val.get("by") or "")}:
            return
        self._page_rec = {"on": bool(val.get("on")), "at": str(val.get("at") or ""), "by": str(val.get("by") or "")}
        at = _parse(val.get("at"))
        if at is None or abs((now - at).total_seconds()) > PAGE_EVENT_FRESH or self.page_event_fn is None:
            return
        try:
            self.page_event_fn(bool(val.get("on")))
        except Exception:       # noqa: BLE001
            pass

    def sync_doc(self, now: datetime = None) -> dict:
        now = (now or datetime.now()).replace(microsecond=0)
        floor = _iso(now - timedelta(days=SYNC_DAYS))
        out = {}
        for rec in self._data.get("history") or []:
            if isinstance(rec, dict) and rec.get("id") and str(rec.get("accepted") or "") >= floor:
                out["i:" + str(rec["id"])] = dict(rec)
        for cur in self.actives():
            if cur.get("id"):
                out["i:" + str(cur["id"])] = self._static(cur)
        for item in self._data.get("pending") or []:
            if isinstance(item, dict):
                out["r:" + self._reward_id(item)] = dict(item, settled=False)
        for rid, item in (self._data.get("settled") or {}).items():
            if isinstance(item, dict):
                out["r:" + str(rid)] = dict(item, settled=True)
        daily = self._data.get("daily") or {}
        if daily.get("date"):
            out["n:" + str(daily["date"])] = {"n_ii": daily.get("n_ii"), "n_iv": daily.get("n_iv")}
        if self._page_rec:
            out["p:page"] = dict(self._page_rec)
        if self.journal_view():
            out["j:view"] = {"at": self.journal_view()}
        return out

    @staticmethod
    def sync_done(value) -> bool:
        """这一项是不是已经到头了 (指令结束 / 奖励结算过): 到头的盖过没到头的."""
        return isinstance(value, dict) and (bool(value.get("result")) or value.get("settled") is True)

    def _all_ids(self) -> set:
        return {str(r.get("id")) for r in (self._data.get("history") or []) if isinstance(r, dict)}

    def sync_apply(self, changes: dict, now: datetime = None):
        """别的设备发来的改动并进来 (界面线程). 完成 / 作废不再给奖励 (奖励有自己的一项), 也不响."""
        now = (now or datetime.now()).replace(microsecond=0)
        floor = _iso(now - timedelta(days=SYNC_DAYS))
        touched = False
        for key, val in (changes or {}).items():
            if not isinstance(val, dict):
                continue
            kind, _, ident = str(key).partition(":")
            if kind == "i":
                touched |= self._adopt_instruction(ident, val, floor)
            elif kind == "r":
                touched |= self._adopt_reward(ident, val)
            elif kind == "p" and ident == "page":
                self._adopt_page(val, now)
            elif kind == "j" and ident == "view":
                at = str(val.get("at") or "")
                if at > self.journal_view():                 # 几台设备谁看得最晚, 冷却就从谁算
                    self._data["journal_view"] = at
                    touched = True
            elif kind == "n" and ident == (self._data.get("daily") or {}).get("date"):
                daily = self._data["daily"]
                for k in ("n_ii", "n_iv"):
                    if isinstance(val.get(k), int) and daily.get(k) != val[k]:
                        daily[k] = val[k]
                        touched = True
        if touched:
            self._recompute_daily(now)
            self._dirty = True
            self.save()
            self._changed()

    def _adopt_instruction(self, ident: str, val: dict, floor: str) -> bool:
        if not ident or str(val.get("accepted") or "") < floor:
            return False
        if ident in self._all_ids():
            return False
        local = next((c for c in self.actives() if str(c.get("id")) == ident), None)
        if val.get("result"):
            if local is not None:
                extra = self._data.get("extra") or []
                if local is self.current:
                    self._data["current"] = extra.pop() if extra else None
                else:
                    extra[:] = [e for e in extra if e is not local]
            history = self._data.setdefault("history", [])
            history.append({k: val.get(k) for k in ("id", "type", "body", "accepted", "ended", "result", "t", "code")})
            history.sort(key=lambda r: str(r.get("ended") or ""))
            if len(history) > HISTORY_KEEP:
                del history[:-HISTORY_KEEP]
            return True
        if local is not None:
            return False
        cur = dict(val, focus_sec=0.0, break_sec=0.0, stats_since=None)
        acts = sorted(self.actives() + [cur], key=lambda c: (str(c.get("accepted") or ""), str(c.get("id"))))
        self._data["current"] = acts[-1]
        self._data["extra"] = acts[:-1]
        return True

    def _adopt_reward(self, ident: str, val: dict) -> bool:
        item = {k: v for k, v in val.items() if k != "settled"}
        item["id"] = ident
        pending = self._data.setdefault("pending", [])
        if val.get("settled") is True:
            before = len(pending)
            pending[:] = [p for p in pending if not (isinstance(p, dict) and self._reward_id(p) == ident)]
            known = ident in self._data.setdefault("settled", {})
            self._data["settled"].setdefault(ident, item)
            return known is False or len(pending) != before
        return self._add_reward(item)
