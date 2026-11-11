"""软规则: 黑雾 / 全频道阻塞干扰 / 通道阻塞 / 禁止软件 / 录像 / 抑制 / 纵深推进 / 静默协议。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

from datetime import datetime

from contract.contract_rules import (
    NET_SHARE, WINDOWS, RECORD, DEEP, FOG_SWITCHES, JAM_BLOCKS, _iso,
)


class WatchMixin:
    def net_term(self) -> str:
        return next((t for t in self.terms if t in NET_SHARE), "")

    def net_check(self, used: float, bonus: float, raw: float, date: str):
        """信息阻断: 合约开始时 (以及合约中换日时) 记下当时的剩余额度, 只能用它的几成, 不能借贷.
        返回 (要降级的词条, "loan" / "over"), 没有违反时 ("", "") (只由做自动结束的设备调用, 额度是各设备共用的).
        已经失效的: 照样按失效时的额度看, 还在继续用超 / 又借贷了就返回它 (只响警报)."""
        tid = self.net_term() or self._lost_of(NET_SHARE)
        if not tid:
            return "", ""
        n = self.live.get("net")
        if not isinstance(n, dict) or n.get("date") != date:
            raw = max(0.0, float(raw))
            self.live["net"] = {"date": date, "base": round(raw, 1), "bonus": round(float(bonus), 1),
                                "limit": round(float(used) + raw * NET_SHARE[tid], 1)}
            self.save()
            return "", ""
        if float(bonus) > float(n.get("bonus") or 0) + 0.5:
            n["bonus"] = round(float(bonus), 1)
            self.save()
            return tid, "loan"
        over = float(used) > float(n.get("limit") or 0) + 0.5
        if tid in self.lost and not self.has(tid):
            w = self.__dict__.setdefault("_watch", {})
            last = w.get("net_used", float(used))
            w["net_used"] = float(used)
            over = over and float(used) > last + 0.5          # 失效后: 还在继续用才算
        if over:
            return tid, "over"
        return "", ""

    def net_left(self, used: float):
        """合约还允许用多少联网额度 (秒); 没有信息阻断时 None."""
        n = self.live.get("net") if self.net_term() else None
        if not isinstance(n, dict):
            return None
        return max(0.0, float(n.get("limit") or 0) - float(used))

    def _edge(self, key: str, over: bool) -> bool:
        """只在刚越过的那一下算一次违反; 回到限额以内后再越过才再算 (只记在本机)."""
        w = self.__dict__.setdefault("_watch", {})
        was = w.get(key, False)
        w[key] = over
        return over and not was

    def _tally(self, kind: str, now: datetime, me: str, limit: int) -> bool:
        """软词条的计数 (黑雾的切页面, 全频道阻塞干扰被禁止的程序): 每台设备记自己的次数, 存在合约里随同步, 各设备加起来算;
        这个钟点里每满 limit 次返回 True (算一次违反, 用掉的记在 used). 换钟点清零."""
        hour = _iso(now.replace(minute=0, second=0, microsecond=0))
        t = self.live.get("tally") if isinstance(self.live.get("tally"), dict) else {}
        k = t.get(kind) if isinstance(t.get(kind), dict) else {}
        if k.get("h") != hour:
            k = {"h": hour, "n": {}, "used": 0}
        n = dict(k.get("n") or {})
        n[me or "?"] = int(n.get(me or "?") or 0) + 1
        k = {"h": hour, "n": n, "used": int(k.get("used") or 0)}
        hit = sum(n.values()) - k["used"] >= limit
        if hit:
            k["used"] += limit
        self.live["tally"] = dict(t, **{kind: k})
        self.save()
        return hit

    def tally_count(self, kind: str, now: datetime) -> int:
        """这个钟点里还没算成违反的次数 (各设备合计)."""
        k = (self.live.get("tally") or {}).get(kind) if self.active else None
        hour = _iso(now.replace(minute=0, second=0, microsecond=0))
        if not isinstance(k, dict) or k.get("h") != hour:
            return 0
        return sum(int(v or 0) for v in (k.get("n") or {}).values()) - int(k.get("used") or 0)

    def fog_switch(self, now: datetime, me: str = "") -> str:
        """黑雾: 合约中要切到计时器以外的页面 (打不开的设置也算) 记一次; 这个钟点里 (各设备合计) 满 FOG_SWITCHES 次返回 "fog"
        (算一次违反, 重新计). 每个钟点清零. 失效后照样数 (再次触发只响警报)."""
        if not self.active or not self.watching("fog"):
            return ""
        return "fog" if self._tally("fog", now, me, FOG_SWITCHES) else ""

    def jam_block(self, now: datetime, me: str = "") -> str:
        """全频道阻塞干扰: 打开的程序 / 应用被名单禁止 (关掉窗口 / 锁住) 记一次; 这个钟点里 (各设备合计) 满 JAM_BLOCKS 次返回 "jam"
        (算一次违反, 重新计). 每个钟点清零. 失效后照样数 (再次触发只响警报)."""
        if not self.active or not self.watching("jam"):
            return ""
        return "jam" if self._tally("jam", now, me, JAM_BLOCKS) else ""

    def window_watch(self):
        """通道阻塞: (要检查的词条, 窗口数上限); 已经失效的按失效前的上限. 没有时 ("", 0)."""
        tid = next((t for t in self.terms if t in WINDOWS), "") or self._lost_of(WINDOWS)
        return tid, (WINDOWS[tid] if tid else 0)

    def window_check(self, n: int) -> str:
        """通道阻塞: 专注中任务栏上的窗口数超过词条的上限 (越过的那一下算一次)."""
        tid, lim = self.window_watch()
        if not lim or not tid:
            return ""
        return tid if self._edge("win", n > lim) else ""

    def window_reset(self):
        """离开专注时段: 通道阻塞的"越过"状态清掉, 下次专注重新算."""
        self._edge("win", False)

    def ban_term(self, kind: str) -> str:
        """禁止打开这一类软件 (BANS 之一) 的是哪个词条: 它自己; 不禁止时 ""."""
        return kind if self.watching(kind) else ""

    def ban_check(self, kind: str, hit: bool) -> str:
        """乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 开着这一类软件.
        每一类只在从没有到有的那一下算一次. 返回要降级的词条."""
        tid = self.ban_term(kind)
        if not tid:
            return ""
        return tid if self._edge(kind, hit) else ""

    def records(self, me: str = None) -> list:
        """合约中要录什么: camera (目光) / screen (刻痕).
        me: 本机的设备 id —— 目光只由开始时选定的那台设备 (cam) 录; 没有记下 cam 的 (旧版本开始的) 各台电脑都录."""
        out = [RECORD[t] for t in self.terms if t in RECORD]
        cam = self.cam
        if me is not None and cam and cam != me and "camera" in out:
            out.remove("camera")
        return out

    @property
    def cam(self) -> str:
        """目光由哪台设备的摄像头录 (设备 id; "" = 没有记下)."""
        return str(self.live.get("cam") or "") if self.active else ""

    def quick_check(self) -> str:
        """抑制: 用了快速访问."""
        return "suppress" if self.watching("suppress") else ""

    def focus_check(self, seconds: float) -> str:
        """纵深推进: 结束的这段专注不到要求的时长."""
        tid = next((t for t in self.terms if t in DEEP), "") or self._lost_of(DEEP)
        return tid if tid and float(seconds) < DEEP[tid] else ""

    def quiet_check(self, hit: bool) -> str:
        """静默协议: 专注中处于联网状态 (只在刚变成这样的那一下算一次)."""
        return "quiet" if self.watching("quiet") and self._edge("quiet", hit) else ""
