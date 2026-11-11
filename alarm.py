"""闹钟 (1.0.359): 设置 → 闹钟 (时间 / 铃声 / 音量) + 沟通的"共时闹钟" (时间跟随对象的实际起床时间).

· 闹钟配置只存本机 (alarm_config.json, 数据文件夹里, 加密), 不同步: 哪台设备响由各自的开关决定.
  共时闹钟的设置 (哪个对象 / 哪些设备响铃) 在 comm/ 里, 随设备同步; 这里只管"这台设备什么时候响".
· 每天重复. 到点之后最多 MISS_SECONDS 内发现的都会响 (程序刚好在忙 / 电脑刚醒); 更晚才发现的这次不响, 直接排到下一次.
  从开启 / 改时间 / 改共时闹钟的那一刻起算: 之前已经过去的钟点不会补响.
· 响铃 = 循环播放铃声直到用户关掉, 最长 RING_SECONDS. 睡眠中 / 合约中 / 任何页面都照响 (弹窗置顶).
· 音量 = 铃声的幅度 (gain = (音量/100)²: 感觉上更接近"一半"); 系统音量不动.

    python alarm.py    自检 (不出声: 音量 0)
"""
import array
import os
import re
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime, timedelta

import config

FILE = "alarm_config.json"
RING_SECONDS = 300                  # 响铃最长多久 (没人关就自己停)
MISS_SECONDS = 900                  # 到点后超过这么久才发现 (程序没开 / 电脑睡着): 这次不响了
PREVIEW_SECONDS = 6                 # 试听最长多久
CLASSIC_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds", "target_alarm.wav")
TONES = (("classic", "经典警报"), ("chime", "三音提示"), ("soft", "柔和钟声"), ("custom", "自选文件"))
DEFAULTS = {"on": False, "time": "07:00", "tone": "classic", "custom": "", "volume": 80,
            "armed": "", "fired": "", "co_sig": ""}

_HM_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$")


def parse_hm(text):
    """"7:05" → (7, 5); 不合法 = None."""
    m = _HM_RE.match(str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)), int(m.group(2))


def fmt_hm(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def next_time(hm, after: datetime) -> datetime:
    """after 之后 (不含) 第一个钟点是 hm 的时刻."""
    t = datetime.combine(after.date(), datetime.min.time()) + timedelta(hours=hm[0], minutes=hm[1])
    return t if t > after else t + timedelta(days=1)


def gain_of(volume) -> float:
    """音量 0-100 → 幅度系数 (平方: 50 约等于听起来的一半)."""
    try:
        v = max(0.0, min(100.0, float(volume))) / 100.0
    except (TypeError, ValueError):
        v = 0.8
    return v * v


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _dt(s):
    try:
        return datetime.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


def day_word(t: datetime, now: datetime) -> str:
    d = (t.date() - now.date()).days
    return {0: "今天", 1: "明天", 2: "后天"}.get(d, f"{t.month}月{t.day}日")


class Alarm:
    """本机的闹钟配置 + "什么时候该响" 的判断 (界面线程每秒调一次 check)."""

    def __init__(self, data_dir: str, now=datetime.now):
        self.dir = data_dir
        self.path = os.path.join(data_dir, FILE)
        self.now = now
        self.cfg = dict(DEFAULTS)
        self.load()

    # ---- 存取 ----
    def load(self):
        d = config.load_json_file(self.path, {})
        if isinstance(d, dict):
            self.cfg.update({k: d[k] for k in DEFAULTS if k in d})
        self.cfg["on"] = bool(self.cfg["on"])
        self.cfg["volume"] = int(max(0, min(100, float(self.cfg["volume"] if isinstance(self.cfg["volume"], (int, float)) else 80))))
        if parse_hm(self.cfg["time"]) is None:
            self.cfg["time"] = DEFAULTS["time"]
        if self.cfg["tone"] not in {k for k, _ in TONES}:
            self.cfg["tone"] = "classic"

    def save(self):
        config.save_json_file(self.path, self.cfg)

    def set(self, **kw):
        """改设置并马上存; 开关 / 时间变了就重新计时 (之前过去的钟点不补响)."""
        re_arm = False
        for k, v in kw.items():
            if k not in DEFAULTS:
                continue
            if k == "time":
                hm = parse_hm(v)
                if hm is None:
                    continue
                v = fmt_hm(*hm)
            if k == "volume":
                v = int(max(0, min(100, float(v))))
            if k == "on":
                v = bool(v)
            if k == "tone" and v not in {t for t, _ in TONES}:
                continue
            if k in ("on", "time") and self.cfg.get(k) != v:
                re_arm = True
            self.cfg[k] = v
        if re_arm:
            self.cfg["armed"] = _iso(self.now())
        self.save()

    def hm(self):
        return parse_hm(self.cfg["time"]) or (7, 0)

    # ---- 什么时候响 ----
    def _ref(self, now: datetime) -> datetime:
        return max(_dt(self.cfg.get("armed")) or now, _dt(self.cfg.get("fired")) or datetime.min)

    def next_ring(self, now: datetime = None, co_fn=None):
        """下一次响铃的时刻 (没有 = None); 不改任何状态. co_fn: 按共时闹钟响铃时的 f(after) → 下一次起床时刻."""
        now = now or self.now()
        if co_fn is None and not self.cfg["on"]:
            return None
        ref = max(self._ref(now), now - timedelta(seconds=MISS_SECONDS))
        return co_fn(ref) if co_fn else next_time(self.hm(), ref)

    def check(self, now: datetime = None, co_fn=None, co_sig: str = ""):
        """每秒调一次. 该响了 → {"at": 预定时刻, "co": 是不是共时闹钟}; 否则 None. 响过 / 错过的会记下来, 下一次排到之后."""
        now = now or self.now()
        sig = co_sig if co_fn else ""
        if sig != self.cfg.get("co_sig"):                     # 换了跟随的对象 / 开关了共时闹钟: 重新计时
            self.cfg["co_sig"] = sig
            self.cfg["armed"] = _iso(now)
            self.save()
        if co_fn is None and not self.cfg["on"]:
            return None
        if not _dt(self.cfg.get("armed")):
            self.cfg["armed"] = _iso(now)
            self.save()
        for _ in range(4):
            ref = self._ref(now)
            nxt = co_fn(ref) if co_fn else next_time(self.hm(), ref)
            if nxt is None or nxt > now:
                return None
            self.cfg["fired"] = _iso(nxt)
            self.save()
            if (now - nxt).total_seconds() <= MISS_SECONDS:
                return {"at": nxt, "co": co_fn is not None}
        return None

    def status_text(self, now: datetime = None, co_fn=None, co_name: str = "") -> str:
        """设置页上一行说明: 下一次什么时候响."""
        now = now or self.now()
        if co_fn is not None:
            t = co_fn(now)
            if t is None:
                return ""
            return f"共时闹钟: 跟随 {co_name} 的起床时间, 下一次 {day_word(t, now)} {t:%H:%M}"
        t = self.next_ring(now)
        return f"下一次: {day_word(t, now)} {t:%H:%M}" if t else "闹钟已关闭"


# ══════════════ 铃声 ══════════════
def tone_path(cfg: dict, data_dir: str) -> str:
    """铃声设置 → 音频文件的路径 (自选文件不见了就用经典警报)."""
    tone = cfg.get("tone")
    if tone == "custom" and cfg.get("custom") and os.path.isfile(cfg["custom"]):
        return cfg["custom"]
    if tone == "chime":
        p = config.ensure_builtin_chime(os.path.join(data_dir, "chime.wav"))
        if p:
            return p
    if tone == "soft":
        p = config.ensure_soft_chime(os.path.join(data_dir, "comm_ping.wav"))
        if p:
            return p
    return CLASSIC_WAV


def scaled_wav(src: str, gain: float, dst: str) -> str:
    """16 / 8 位 PCM 的 wav 乘上 gain 写到 dst, 返回 dst; 别的格式 / 出错 = 原文件 src (音量调不了)."""
    try:
        with wave.open(src, "rb") as w:
            params = w.getparams()
            frames = w.readframes(w.getnframes())
        if params.comptype != "NONE":
            return src
        if params.sampwidth == 2:
            a = array.array("h")
            a.frombytes(frames)
            if sys.byteorder == "big":
                a.byteswap()
            out = array.array("h", (max(-32768, min(32767, int(x * gain))) for x in a))
            if sys.byteorder == "big":
                out.byteswap()
            data = out.tobytes()
        elif params.sampwidth == 1:
            data = bytes(max(0, min(255, int((x - 128) * gain) + 128)) for x in frames)
        else:
            return src
        with wave.open(dst, "wb") as w:
            w.setparams(params)
            w.writeframes(data)
        return dst
    except Exception:       # noqa: BLE001
        return src


def _play_wav(path: str, ev: threading.Event) -> bool:
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return True
    except Exception:       # noqa: BLE001
        return False


def _play_mci(path: str, gain: float, ev: threading.Event) -> bool:
    """其他格式 (mp3 等) 用 Windows 自带的 MCI 播放, 边放边看有没有被要求停; 音量用 setaudio (0-1000)."""
    try:
        import ctypes
        import uuid
        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(256)
        alias = "oit_alarm_" + uuid.uuid4().hex[:8]
        if mci(f'open "{os.path.normpath(os.path.abspath(path))}" type mpegvideo alias {alias}', buf, 255, 0) != 0:
            return False
        try:
            mci(f"setaudio {alias} volume to {int(max(0.0, min(1.0, gain)) * 1000)}", buf, 255, 0)
            if mci(f"play {alias}", buf, 255, 0) != 0:
                return False
            while not ev.is_set():
                if mci(f"status {alias} mode", buf, 255, 0) != 0 or buf.value.strip().lower() != "playing":
                    break
                ev.wait(0.1)
            return True
        finally:
            mci(f"stop {alias}", buf, 255, 0)
            mci(f"close {alias}", buf, 255, 0)
    except Exception:       # noqa: BLE001
        return False


class Ringer:
    """循环播放铃声直到 stop() (或超过时间). 后台线程里放, 界面线程调 start / stop."""

    def __init__(self, data_dir: str):
        self.dir = data_dir
        self._ev = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    @property
    def ringing(self) -> bool:
        t = self._thread
        return bool(t and t.is_alive() and not self._ev.is_set())

    def start(self, cfg: dict, seconds: float = RING_SECONDS):
        self.stop()
        if sys.platform != "win32":
            return
        ev = threading.Event()
        path = tone_path(cfg, self.dir)
        gain = gain_of(cfg.get("volume", 80))
        with self._lock:
            self._ev = ev
            self._thread = threading.Thread(target=self._run, args=(ev, path, gain, seconds), daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            ev, t = self._ev, self._thread
            ev.set()
        if sys.platform == "win32":
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:       # noqa: BLE001
                pass
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)

    def _run(self, ev: threading.Event, path: str, gain: float, seconds: float):
        end = time.monotonic() + seconds
        play = None
        if path.lower().endswith(".wav"):
            scaled = scaled_wav(path, gain, os.path.join(self.dir, "alarm_play.wav"))
            path = scaled
            play = lambda p: _play_wav(p, ev)           # noqa: E731
        else:
            play = lambda p: _play_mci(p, gain, ev)     # noqa: E731
        while not ev.is_set() and time.monotonic() < end:
            ok = play(path)
            if ev.is_set():
                break
            if not ok:                                    # 放不出来 (没有声卡 / 格式不支持): 蜂鸣, 音量 0 = 不出声
                if gain > 0:
                    try:
                        import winsound
                        winsound.Beep(1400, 200)
                    except Exception:       # noqa: BLE001
                        pass
                ev.wait(0.8)
            else:
                ev.wait(0.7)


# ══════════════ 自检 ══════════════
def _selftest():
    td = tempfile.mkdtemp()
    t0 = datetime(2027, 6, 1, 6, 0)
    clock = [t0]
    assert parse_hm("7:05") == (7, 5) and parse_hm(" 07 ： 05 ") == (7, 5) and parse_hm("24:00") is None and parse_hm("7:60") is None and parse_hm("x") is None
    assert next_time((7, 0), datetime(2027, 6, 1, 6, 59, 59)) == datetime(2027, 6, 1, 7, 0)
    assert next_time((7, 0), datetime(2027, 6, 1, 7, 0)) == datetime(2027, 6, 2, 7, 0), "同一分钟已经过了: 明天"
    assert gain_of(100) == 1.0 and gain_of(0) == 0.0 and abs(gain_of(50) - 0.25) < 1e-9 and abs(gain_of("x") - 0.64) < 1e-9

    a = Alarm(td, now=lambda: clock[0])
    assert a.cfg["on"] is False and a.cfg["time"] == "07:00" and a.cfg["tone"] == "classic" and a.cfg["volume"] == 80
    assert a.check() is None and a.next_ring() is None and a.status_text() == "闹钟已关闭"
    a.set(on=True, time="6:30")
    assert a.cfg["time"] == "06:30" and a.next_ring() == datetime(2027, 6, 1, 6, 30) and "今天 06:30" in a.status_text()
    a.set(time="bad", volume=250, tone="nope")
    assert a.cfg["time"] == "06:30" and a.cfg["volume"] == 100 and a.cfg["tone"] == "classic", "不合法的输入不改 / 音量夹在 0-100"
    clock[0] = datetime(2027, 6, 1, 6, 29, 59)
    assert a.check() is None
    clock[0] = datetime(2027, 6, 1, 6, 30, 0)
    hit = a.check()
    assert hit and hit["at"] == datetime(2027, 6, 1, 6, 30) and hit["co"] is False
    assert a.check() is None, "响过的不再响"
    assert a.next_ring() == datetime(2027, 6, 2, 6, 30), "每天重复"
    b = Alarm(td, now=lambda: clock[0])
    assert b.cfg["fired"] == a.cfg["fired"] and b.cfg["time"] == "06:30" and b.check() is None, "存盘, 重新打开不会再响一次"
    # 错过: 程序 / 电脑睡着, 超过 15 分钟才发现 = 这次不响
    clock[0] = datetime(2027, 6, 2, 6, 50)
    assert a.check() is None and a.next_ring() == datetime(2027, 6, 3, 6, 30), "晚了 20 分钟: 这次不响了"
    clock[0] = datetime(2027, 6, 3, 6, 40)
    assert a.check() and a.check() is None, "10 分钟内发现的照响"
    # 改时间 / 开关: 之前过去的钟点不补响
    clock[0] = datetime(2027, 6, 3, 12, 0)
    a.set(time="11:00")
    assert a.check() is None and a.next_ring() == datetime(2027, 6, 4, 11, 0), "新设的时间今天已经过了: 排明天"
    a.set(on=False)
    clock[0] = datetime(2027, 6, 4, 11, 0)
    assert a.check() is None
    # 共时闹钟: 时间由 co_fn 给, 开关不影响; 换对象 (co_sig) 重新计时
    wakes = {"A": [datetime(2027, 6, 5, 6, 12), datetime(2027, 6, 6, 6, 25)]}

    def co_a(after):
        return next((w for w in wakes["A"] if w > after), None)

    clock[0] = datetime(2027, 6, 4, 23, 0)
    assert a.check(clock[0], co_a, "A") is None and a.cfg["co_sig"] == "A"
    assert a.next_ring(clock[0], co_a) == datetime(2027, 6, 5, 6, 12) and "共时闹钟" in a.status_text(clock[0], co_a, "Claudia") and "明天 06:12" in a.status_text(clock[0], co_a, "Claudia")
    clock[0] = datetime(2027, 6, 5, 6, 12, 1)
    hit = a.check(clock[0], co_a, "A")
    assert hit and hit["co"] is True and hit["at"] == datetime(2027, 6, 5, 6, 12)
    assert a.check(clock[0], co_a, "A") is None
    clock[0] = datetime(2027, 6, 6, 6, 25, 30)
    assert a.check(clock[0], co_a, "A")
    assert a.check(clock[0], co_a, "A") is None and a.check(clock[0], lambda after: None, "A") is None, "算不出下一次起床 = 不响"
    a.set(on=True, time="07:00")
    clock[0] = datetime(2027, 6, 6, 12, 0)
    assert a.check(clock[0], None, "") is None and a.cfg["co_sig"] == "", "关掉共时闹钟: 回到自己的闹钟, 重新计时"
    clock[0] = datetime(2027, 6, 7, 7, 0)
    assert a.check(clock[0]) and a.next_ring(clock[0]) == datetime(2027, 6, 8, 7, 0)

    # 铃声: 路径 / 音量缩放
    assert tone_path({"tone": "classic"}, td) == CLASSIC_WAV and os.path.exists(CLASSIC_WAV)
    assert tone_path({"tone": "custom", "custom": os.path.join(td, "没有.mp3")}, td) == CLASSIC_WAV, "自选文件不见了: 经典警报"
    ch, so = tone_path({"tone": "chime"}, td), tone_path({"tone": "soft"}, td)
    assert os.path.basename(ch) == "chime.wav" and os.path.basename(so) == "comm_ping.wav" and os.path.getsize(ch) > 44 and os.path.getsize(so) > 44

    def peak(p):
        with wave.open(p, "rb") as w:
            x = array.array("h")
            x.frombytes(w.readframes(w.getnframes()))
        return max(abs(v) for v in x)

    base = peak(ch)
    half = scaled_wav(ch, 0.25, os.path.join(td, "half.wav"))
    assert half != ch and abs(peak(half) - base * 0.25) <= 2, "音量缩放"
    zero = scaled_wav(ch, 0.0, os.path.join(td, "zero.wav"))
    assert peak(zero) == 0
    assert scaled_wav(os.path.join(td, "没有.wav"), 0.5, os.path.join(td, "x.wav")) == os.path.join(td, "没有.wav"), "读不了: 原文件"
    # 响铃线程 (音量 0, 不出声): 能开能停, 到时间自己停
    if sys.platform == "win32":
        r = Ringer(td)
        r.start({"tone": "chime", "volume": 0}, seconds=30)
        time.sleep(0.4)
        assert r.ringing
        t_stop = time.monotonic()
        r.stop()
        assert not r.ringing and time.monotonic() - t_stop < 3
        r.start({"tone": "soft", "volume": 0}, seconds=0.3)
        time.sleep(2.5)
        assert not (r._thread and r._thread.is_alive()), "到时间自己停"
        r.stop()
    return "alarm selftest OK"


if __name__ == "__main__":
    print(_selftest())
