"""
系统时间被改 (MECHANICS.md 9.6): 警告一次, 10 分钟内改回来, 否则 (或者再改一次) 清掉数据
──────────────────────────────────────────────────────────────────────────────────────
冷静期 (120 h)、设置的等待 (48 h)、每日额度、休息、录像的保留期 …… OIT 的好几处规则靠系统时间, 改系统时间就能躲开它们.

怎么发现: 系统时间 (time.time) 减去开机以来的时间 (QueryInterruptTime, 睡眠 / 休眠也算在里面, 不受改时间影响) 就是"开机的时刻",
同一次开机里它不会变; 它一下子变了 (超过 TOL = 5 分钟) = 系统时间被改了, 改了多少一目了然. 睡眠 / 休眠不会引起误判 (两个量一起走).
慢慢漂移 (< FOLLOW) 的跟着走; 5 分钟以内的小调整 (时间同步) 不理. 程序没开着的时候改的: 重新打开时发现时间比上次记着的还早 (往回改) 才算,
往前改的无法和"关机了很久"区分, 不管.

怎么处理 (状态存在数据文件夹的 clock_guard.json 里, 加密, 重启不丢):
  · 第一次发现 → 弹窗 "<用户名>,这没有意义.你可以在10min内修改回来,否则你的数据会被清除.", 开始 10 分钟 (GRACE, 只在程序运行时走) 倒计时.
  · 倒计时里系统时间回到原来的 (开机时刻和改之前的差在 TOL 以内) → 解除. 重启过 / 重新开机过的, 按"现在的时间离改之前最后的时间有多远"估计.
  · 倒计时走完还没改回来, 或者倒计时里又改了一次 (改成别的时间) → 清掉数据 (和连续性不对时一样: 标记 pending, 重启, 下一次启动清数据并弹出文本).
  · 警告过一次之后 24 小时 (REPEAT) 内再发现被改 (哪怕上一次改回来了) → 直接清数据.

    python clockguard.py       自检 (时钟和存储都是假的)
"""

import ctypes
import json
import os
import sys
import time

FILE = "clock_guard.json"
TOL = 300.0               # 秒: "开机时刻"变了超过这么多 = 时间被改了
FOLLOW = 30.0             # 秒: 变化小于这个的当作时钟漂移, 基准跟着走
GRACE = 600.0             # 秒: 警告之后改回来的时间 (程序运行时才走)
REPEAT = 24 * 3600.0      # 秒: 警告之后这么久之内再被改 = 直接清数据
SAVE_EVERY = 30.0


def uptime():
    """开机以来的秒数 (睡眠 / 休眠也算, 不受系统时间影响). 取不到返回 None (这时不检测)."""
    if sys.platform != "win32":
        return None
    try:
        k = ctypes.WinDLL("kernelbase")
        v = ctypes.c_ulonglong()
        f = k.QueryInterruptTime
        f.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        f(ctypes.byref(v))
        return v.value / 1e7
    except Exception:
        return None


def warn_message(username: str) -> str:
    return f"{username or ''},这没有意义.你可以在10min内修改回来,否则你的数据会被清除."


class ClockGuard:
    def __init__(self, data_dir: str, wall=time.time, up=uptime, load=None, save=None, log=lambda s: None):
        self.dir = data_dir
        self.path = os.path.join(data_dir, FILE)
        self.wall, self.up, self.log = wall, up, log
        self._load = load or self._load_file
        self._save = save or self._save_file
        self.on = False
        self.ref = 0.0              # 基准: 没被改过的"开机时刻"
        self.last_u = 0.0
        self.warn = None            # 倒计时中: {"kind": "jump"/"back", "d": 改了多少秒, "ref": 基准, "legit": 改之前应有的时间, "left": 剩余秒, "up": 上次记的开机时间}
        self.last_warn = 0.0        # 上一次警告时 (没被改的) 时间
        self._saved_at = -1e9
        self._event = None
        self._wiped = False          # 已经发出过 "wipe": 不再重复

    # ---- 存取 ----

    def _load_file(self):
        try:
            import securefile
            with securefile.open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def _save_file(self, d: dict):
        try:
            import securefile
            os.makedirs(self.dir, exist_ok=True)
            with securefile.open(self.path, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except OSError:
            pass

    def snapshot(self) -> dict:
        return {"ref": self.ref, "up": self.last_u, "wall": self.wall(), "warn": self.warn, "last_warn": self.last_warn}

    def _persist(self, force: bool = False):
        if force or self.last_u - self._saved_at >= SAVE_EVERY:
            self._saved_at = self.last_u
            self._save(self.snapshot())

    # ---- 启动 ----

    def start(self):
        """程序启动时 (数据核对之后): 读上次记的, 看有没有在程序没开着时被往回改, 有没有没走完的倒计时. 发现的事件由第一次 tick 交出去."""
        u = self.up()
        if u is None:
            return
        self.on = True
        w = self.wall()
        est = w - u
        d = self._load()
        self.ref, self.last_u = est, u
        if not d:
            self._persist(True)
            return
        try:
            ref0, up0, wall0 = float(d.get("ref")), float(d.get("up")), float(d.get("wall"))
            self.last_warn = float(d.get("last_warn") or 0.0)
        except (TypeError, ValueError):
            self._persist(True)
            return
        same_boot = u >= up0
        pw = d.get("warn") if isinstance(d.get("warn"), dict) else None
        if pw:
            self._resume_warn(pw, est, u, w, same_boot)
            self._persist(True)
            return
        if same_boot and abs(est - ref0) <= TOL:
            self.ref = ref0 if abs(est - ref0) >= FOLLOW else est
        elif w < wall0 - TOL:
            # 程序没开着的时候 (或者重新开机时) 系统时间比上次记着的还早: 往回改了
            self._event = self._detect("back", w - wall0, wall0, u, est)
        self._persist(True)

    def _resume_warn(self, pw: dict, est: float, u: float, w: float, same_boot: bool):
        try:
            kind, dd, legit = str(pw["kind"]), float(pw["d"]), float(pw["legit"])
            left, ref = float(pw["left"]), float(pw["ref"])
        except (KeyError, TypeError, ValueError):
            return
        if kind == "back":
            restored = w >= legit - TOL
            self.ref = est
        elif same_boot and abs(est - ref) <= TOL:
            restored, self.ref = True, est
        elif same_boot and abs(est - (ref + dd)) <= TOL:
            restored, self.ref = False, ref                       # 同一次开机, 还是被改的状态
        else:
            apparent = w - legit                                  # 重新开机了: 离改之前最后的时间有多远
            restored = apparent < dd / 2 if dd > 0 else apparent > dd / 2
            self.ref = est if restored else est - dd
        if restored:
            self.warn = None
            self._event = "restored"
        else:
            self.warn = {"kind": kind, "d": dd, "ref": self.ref, "legit": legit, "left": left, "up": u}

    # ---- 发现 ----

    def _detect(self, kind: str, delta: float, legit: float, u: float, est: float):
        """第一次发现被改: 警告, 或者 (24 小时内警告过) 直接清数据. 返回事件."""
        if self.last_warn and 0 <= legit - self.last_warn < REPEAT:
            self.log(f"clock: modified again within {REPEAT / 3600:g} h of the warning")
            return "wipe"
        self.last_warn = legit
        self.warn = {"kind": kind, "d": delta, "ref": self.ref, "legit": legit, "left": GRACE, "up": u}
        self.log(f"clock: modified by {delta:+.0f}s ({kind}), {GRACE:.0f}s to restore")
        return "warn"

    def tick(self):
        """大约每秒一次. 返回事件: "warn" (第一次发现, 要弹窗) / "restored" (改回来了) / "wipe" (要清数据) / None."""
        if not self.on or self._wiped:
            return None
        u = self.up()
        if u is None:
            return None
        ev, self._event = self._event, None
        w = self.wall()
        est = w - u
        self.last_u = u
        if ev is None:
            if self.warn is None:
                delta = est - self.ref
                if abs(delta) <= TOL:
                    if abs(delta) < FOLLOW:
                        self.ref = est
                else:
                    ev = self._detect("jump", delta, self.ref + u, u, est)
            else:
                ev = self._in_grace(w, u, est)
        if ev == "wipe":
            self._wiped = True
        if ev in ("warn", "restored", "wipe"):
            self._persist(True)
        else:
            self._persist()
        return ev

    def _in_grace(self, w: float, u: float, est: float):
        g = self.warn
        restored = (w >= g["legit"] - TOL) if g["kind"] == "back" else abs(est - self.ref) <= TOL
        if restored:
            self.warn = None
            if g["kind"] != "back":
                self.ref = est if abs(est - self.ref) < FOLLOW else self.ref
            else:
                self.ref = est
            return "restored"
        if g["kind"] == "jump" and abs(est - (self.ref + g["d"])) > TOL:
            self.log("clock: modified a second time during the grace period")
            return "wipe"
        g["left"] = float(g["left"]) - max(0.0, u - float(g["up"]))
        g["up"] = u
        if g["left"] <= 0:
            self.log("clock: not restored within the grace period")
            return "wipe"
        return None


# ---- 自检 ----

def _selftest():
    class Clock:
        """假的时钟: 开机时刻 boot + 开机以来的秒 u; set_wall 改系统时间 (不动 u)."""
        def __init__(self, wall=1_800_000_000.0, u=1000.0):
            self.offset = wall - u          # 系统时间 = offset + u
            self.u = u

        def wall(self):
            return self.offset + self.u

        def up(self):
            return self.u

        def run(self, secs):
            self.u += secs

        def shift(self, secs):
            self.offset += secs

    store = {}

    def mk(c, **kw):
        g = ClockGuard("x", wall=c.wall, up=c.up, load=lambda: json.loads(json.dumps(store["d"])) if "d" in store else None,
                       save=lambda d: store.__setitem__("d", json.loads(json.dumps(d))), **kw)
        g.start()
        return g

    def run(g, c, secs, step=1.0):
        evs = []
        n = int(secs / step)
        for _ in range(n):
            c.run(step)
            e = g.tick()
            if e:
                evs.append(e)
        return evs

    # 正常走: 没有事件; 小调整 (时间同步) 和睡眠 (u 一起走) 不算
    c = Clock()
    g = mk(c)
    assert run(g, c, 120) == []
    c.shift(200)                                      # 3 分钟的小调整: 不理
    assert run(g, c, 5) == []
    c.run(8 * 3600)                                   # 睡了 8 小时 (u 和系统时间一起走)
    assert g.tick() is None and run(g, c, 3) == []
    # 往前改 3 天: 警告; 10 分钟内改回来 → 解除
    c.shift(3 * 86400)
    assert g.tick() == "warn" and g.warn["kind"] == "jump" and abs(g.warn["d"] - 3 * 86400) < TOL
    assert run(g, c, 120) == []
    c.shift(-3 * 86400)
    assert g.tick() == "restored" and g.warn is None
    assert run(g, c, 60) == []
    # 24 小时内再被改 (哪怕改回来了): 直接清数据
    c.shift(-2 * 3600)
    assert g.tick() == "wipe"
    # 警告之后 10 分钟没改回来 → 清数据
    store.clear()
    c = Clock()
    g = mk(c)
    run(g, c, 10)
    c.shift(-86400)                                   # 往回改一天
    assert g.tick() == "warn"
    ev = run(g, c, GRACE + 5)
    assert ev == ["wipe"], ev
    # 倒计时里又改了一次 (改成别的时间) → 清数据; 改回来之前一直不动则不算
    store.clear()
    c = Clock()
    g = mk(c)
    run(g, c, 10)
    c.shift(86400)
    assert g.tick() == "warn"
    assert run(g, c, 100) == []
    c.shift(7200)                                     # 又改了 2 小时
    assert g.tick() == "wipe"
    # 重启 (同一次开机) 时倒计时还在: 还没改回来 → 接着倒数; 改回来了 → 解除
    store.clear()
    c = Clock()
    g = mk(c)
    run(g, c, 10)
    c.shift(86400)
    assert g.tick() == "warn"
    run(g, c, 100)
    g._persist(True)
    g2 = mk(c)                                        # 程序重新打开
    assert g2.warn and 0 < g2.warn["left"] < GRACE and g2.tick() is None and g2.warn is not None
    c.shift(-86400)
    g3 = mk(c)
    assert g3.tick() == "restored" and g3.warn is None, "重启前改回来了"
    # 重新开机之后: 按离改之前最后的时间有多远估计
    store.clear()
    c = Clock()
    g = mk(c)
    run(g, c, 10)
    c.shift(86400)                                    # 往前改一天
    assert g.tick() == "warn"
    g._persist(True)
    c2 = Clock(wall=c.wall() + 300, u=50.0)           # 重新开机, 系统时间还是被改的 (比改之前最后的时间晚一天多)
    g2 = mk(c2)
    assert g2.warn is not None and g2.tick() is None, "重新开机后还是被改的: 倒计时继续"
    c3 = Clock(wall=c.wall() - 86400 + 300, u=50.0)   # 重新开机, 系统时间是对的 (离改之前最后的时间只有几分钟)
    g3 = mk(c3)
    assert g3.warn is None and g3.tick() == "restored", "重新开机后时间是对的: 解除"
    # 程序没开着的时候往回改 (重新打开时比上次记着的还早): 警告, 回到记着的时间之后解除
    store.clear()
    c = Clock()
    g = mk(c)
    run(g, c, 60)
    g._persist(True)
    last = c.wall()
    c2 = Clock(wall=last - 5 * 86400, u=c.u + 30)     # 同一次开机里, 系统时间往回改了 5 天
    g2 = mk(c2)
    assert g2.tick() == "warn" and g2.warn["kind"] == "back"
    c2.shift(5 * 86400 + 60)
    assert g2.tick() == "restored"
    # 取不到开机时间 (非 Windows): 什么都不做
    g4 = ClockGuard("x", up=lambda: None, load=lambda: None, save=lambda d: None)
    g4.start()
    assert g4.tick() is None and not g4.on
    assert warn_message("小明") == "小明,这没有意义.你可以在10min内修改回来,否则你的数据会被清除."
    # 真实的开机时间取得到
    if sys.platform == "win32":
        assert uptime() and uptime() > 1
    return "clockguard selftest OK"


if __name__ == "__main__":
    print(_selftest())
