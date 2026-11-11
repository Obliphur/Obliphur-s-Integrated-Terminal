"""指令的完成 / 作废与每秒判定: 页面停留、合约匹配、计时 (MECHANICS 22.4 ~ 22.7)。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime

from prescript_parts.prescript_common import (
    BREAK_TARGET, FIXED_T, FOCUS_TARGET, HISTORY_KEEP, IDLE_ENTRIES, REFLECT_TARGET, _iso, _parse,
    code_for, contract_duration_hours,
)


class FlowMixin:
    def _finish(self, result: str, now: datetime, t=None, save: bool = True,
                notify: bool = True, settle: bool = True, cur: dict = None):
        cur = cur if cur is not None else self.current
        if cur is None:
            return None
        done = None
        if result == "completed":
            done = float(t if t is not None else cur.get("t") or 0.0)
        rec = {
            "id": cur.get("id", ""),
            "type": cur.get("type", ""),
            "body": cur.get("body", ""),
            "accepted": cur.get("accepted"),
            "ended": _iso(now),
            "result": result,
            "t": done,
            "code": code_for(done) if result == "completed" else "",
        }
        extra = self._data.get("extra") or []
        if cur is self.current:
            # 当前这条结束了: 后面留着的最新一条顶上来.
            self._data["current"] = extra.pop() if extra else None
        else:
            extra[:] = [e for e in extra if e is not cur]
        history = self._data.setdefault("history", [])
        history.append(rec)
        if len(history) > HISTORY_KEEP:
            del history[:-HISTORY_KEEP]

        if result == "completed":
            self._add_reward({
                "id": rec["id"],
                "kind": "complete",
                "code": code_for(done),
                "t": done,
                "seconds": int(round(float(done) * 180)),
                "at": _iso(now),
            })
            self._recompute_daily(now)
            self._check_daily_bonus(now)
        self._dirty = True
        if save:
            self.save()
        if result == "completed" and settle and self._page() == "prescript":
            self.settle_rewards(now)
        if notify:
            self._changed()
        if result == "completed" and self.on_complete is not None:
            try:
                self.on_complete()
            except Exception:       # noqa: BLE001
                pass
        return rec

    def check_visible(self, now: datetime = None):
        """空闲词条: 页面 / 窗口刚一变化就判一次 (不用等下一秒的 tick)."""
        if self.on_page() or self._peers_on_page():
            return
        for cur in list(self.actives()):
            if cur.get("type") in IDLE_ENTRIES:
                now = (now or datetime.now()).replace(microsecond=0)
                self._finish("void", now, cur=cur)

    def page_changed(self, now: datetime = None):
        """切页 / 最小化时调用: 空闲词条立即作废, VI 的连续停留立即清零."""
        self.check_visible(now)
        for cur in self.actives():
            if cur.get("type") == "VI" and cur.get("stats_since"):
                if self._page() != "stats" or self._minimized():
                    cur["stats_since"] = None
                    self._dirty = True
                    self._save_if_due(now or datetime.now())

    def _matching_contract(self, cur: dict):
        """II: 领取之后完成、等级够的下一次合约 (取最早完成的那一个)."""
        accepted = _parse(cur.get("accepted"))
        expires = _parse(cur.get("expires"))
        try:
            need = int(cur.get("n") or 0)
        except (TypeError, ValueError):
            need = 0
        best, best_end = None, None
        for rec in self.contracts_fn() or []:
            if not isinstance(rec, dict) or rec.get("result") != "completed":
                continue
            end = _parse(rec.get("end"))
            if end is None or accepted is None or end <= accepted:
                continue
            if expires is not None and end > expires:
                continue
            try:
                level = int(rec.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            if level < need:
                continue
            if best_end is None or end < best_end:
                best, best_end = rec, end
        return best

    def tick(self, now: datetime = None):
        """每秒: 累计专注 / 休息, 看合约 / 统计页 / 当天专注, 处理作废."""
        now = (now or datetime.now()).replace(microsecond=0)
        if self.blocked_fn():
            return
        self._ensure_day(now)
        self._check_dawn(now)
        prev = self._last_tick
        delta = max(0.0, (now - prev).total_seconds())
        self._last_tick = now

        for cur in list(self.actives()):
            self._tick_one(cur, now, prev, delta)
        self._save_if_due(now)

    def _tick_one(self, cur: dict, now: datetime, prev: datetime, delta: float):
        kind = cur.get("type", "")
        expires = _parse(cur.get("expires"))

        if kind in IDLE_ENTRIES:
            here = self.on_page()
            if not here and not self._peers_on_page():
                self._finish("void", now, cur=cur)               # 所有设备都离开了指令页 / 最小化: 作废
                return
            # 自动完成的时刻 (旧存档里的 V 只有 expires); X 没有计时, 等输入框保存.
            # 只有停在指令页的设备去完成 (别的设备还停在页面上时, 由它来完成)
            due = _parse(cur.get("due")) or _parse(cur.get("expires"))
            if here and due is not None and now >= due:
                self._finish("completed", now, float(cur.get("t") or FIXED_T.get(kind, 0.1)), cur=cur)
                return
        elif kind == "I":
            # 只算到作废时刻为止: 过了 1h 之后的那一段不能算进 50min.
            gain = delta
            if expires is not None:
                cutoff = min(now, expires)
                gain = max(0.0, (cutoff - prev).total_seconds()) if cutoff > prev else 0.0
            if self.state_fn() == "focus":
                cur["focus_sec"] = float(cur.get("focus_sec", 0.0) or 0.0) + gain
                self._dirty = True
            if float(cur.get("focus_sec", 0.0) or 0.0) >= FOCUS_TARGET:
                self._finish("completed", now, 1.0, cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "III":
            gain = delta
            if expires is not None:
                cutoff = min(now, expires)
                gain = max(0.0, (cutoff - prev).total_seconds()) if cutoff > prev else 0.0
            if self.state_fn() == "break":
                cur["break_sec"] = float(cur.get("break_sec", 0.0) or 0.0) + gain
                self._dirty = True
            target = float(cur.get("n") or 0) * 60 or BREAK_TARGET
            if float(cur.get("break_sec", 0.0) or 0.0) >= target:
                self._finish("completed", now, float(cur.get("t") or 1.0), cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "II":
            rec = self._matching_contract(cur)
            if rec is not None:
                t = contract_duration_hours(rec)
                self._finish("completed", now, t, cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "IV":
            daily = self._daily(now)
            try:
                hours = float(self.today_hours_fn())
            except (TypeError, ValueError):
                hours = 0.0
            if hours >= float(daily.get("n_iv", 0) or 0) - 1e-9:
                self._finish("completed", now, 1.0, cur=cur)
                return
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return
        elif kind == "VI":
            if self._page() == "stats" and not self._minimized():
                if not cur.get("stats_since"):
                    cur["stats_since"] = _iso(now)
                    self._dirty = True
                since = _parse(cur.get("stats_since"))
                if since is not None and (now - since).total_seconds() >= REFLECT_TARGET:
                    self._finish("completed", now, 1.0, cur=cur)
                    return
            elif cur.get("stats_since"):
                cur["stats_since"] = None
                self._dirty = True
            if expires is not None and now >= expires:
                self._finish("expired", now, cur=cur)
                return

    def on_page(self) -> bool:
        """本机此刻是不是停在指令页上 (没有最小化)."""
        return self._page() == "prescript" and not self._minimized()

    def _peers_on_page(self) -> bool:
        try:
            return bool(self.peers_on_page_fn())
        except Exception:       # noqa: BLE001
            return False
