"""
设备同步: 本机数据 <-> devsync 的文档 / 账 / 状态 (MECHANICS.md 第 15 节).
所有方法都在界面线程里调用 (devsync.MainRunner 负责转过来).
"""

import tempfile
from datetime import datetime

import config
import i18n
import toast
from i18n import tr
from settings_manager import SCHEMA_BY_KEY
import devsync
import os


def _norm_setting(key: str, value):
    """两端的设置值统一成: 数值项是数字, 其余是文字 ("1", "0.9", "blacklist" …)."""
    spec = SCHEMA_BY_KEY.get(key) or {}
    if spec.get("kind") == "num":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, float) and value.is_integer() and spec.get("kind") == "choice":
        value = int(value)
    return "" if value is None else str(value)


class PcLocal:

    def __init__(self, app):
        self.app = app

    # ---- 基本 ----

    def net_mode(self) -> str:
        return self.app.net.net_mode

    def app_version(self) -> str:
        return config.APP_VERSION

    # ---- 开发者模式: 子端跟着母端 ----

    def dev_mode(self) -> bool:
        return config.dev_mode_enabled()

    def set_dev_mode(self, on: bool):
        self.app.follow_dev_mode(bool(on))

    # ---- 其他设备直接发来的安装包 (MECHANICS 16) ----

    def update_wanted(self, platform: str, version: str) -> str:
        return self.app.update_wanted(platform, version)

    def update_dest(self, version: str) -> str:
        fd, path = tempfile.mkstemp(prefix=f"oit-{version}-", suffix=".zip")
        os.close(fd)
        return path

    def update_received(self, path: str, version: str, name: str):
        self.app.on_update_received(path, version, name)

    def update_requested(self, peer_id: str) -> str:
        """绑定的设备请本机给它下载新版本并发过去: "" = 好, 否则是原因."""
        return "" if self.app.relay_updates(manual=True, only=peer_id) else "busy"

    def update_failed(self, err: str):
        toast.warn(tr("接收新版本失败: {e}", e=err))

    def file_offered(self, peer_id: str, who: str, o: dict) -> str:
        """文件通道 (MECHANICS 15.6): 别的设备要发文件过来, 问用户存到哪里."""
        return self.app.file_channel.offered(peer_id, who, o)

    def file_delivered(self, who: str, name: str):
        self.app.file_channel.delivered(who, name)

    def rec_offered(self, peer_id: str, who: str, o: dict) -> str:
        """录像 (MECHANICS 18.8): 别的设备把它录的片段 / 合并好的最终录像发过来. 自动收下, 不问用户."""
        return self.app.recording_offered(peer_id, who, o)

    def rec_deleted(self, cid: str, kind: str):
        """录像 (MECHANICS 18.8): 别的设备删了某个录像, 本机也删掉."""
        self.app.rec_deleted(cid, kind)

    def rec_stop(self, cid: str):
        """录像 (MECHANICS 18.8): 别的设备终止了录制, 本机也停 (违反随合约同步)."""
        self.app.rec_stop(cid)

    def wipe_requested(self, wipe_id: str, sender: str, name: str, leave: bool = False):
        """别的设备重置 / 卸载了, 请本机也清空数据 (MECHANICS 9): 回复对方之后再做 (重置会关闭程序).
        leave: 它解除了和本机的绑定 (15.1): 只重置本机, 不转告其他设备."""
        self.app.root.after(300, lambda: self.app.do_reset(wipe_id=wipe_id, exclude=(sender,), by=name, leave=leave))

    def auto_text(self) -> str:
        return self.app.dm.auto_text

    def auto_norm(self, text: str) -> str:
        """别的设备 (界面语言可能不同) 写的 Sleeping / 睡眠 之类的默认内容, 换成本机的语言."""
        return i18n.auto_names_in(text)

    def auto_text_for(self, key: str) -> str:
        """整点记录里这一格的自动填充内容 (行为模式会按小时变, MECHANICS 21)."""
        return self.app.dm.auto_text_for_key(key)

    def changed(self):
        try:
            self.app.on_sync_changed()
        except Exception:
            pass

    # ---- 账: 本机自己的数据 ----

    def ledger(self) -> dict:
        dm, n = self.app.dm, self.app.net
        return {
            "net": {"date": n.date, "used": round(n.used, 1), "bonus": n.bonus,
                    "tomorrow_used": n.tomorrow_used, "debt": n.debt, "gift": n.gift},
            "study": {k: round(v, 4) for k, v in dm.study_data.items()},
            "focus": [dict(r) for r in dm.focus_sessions],
            "usage": [dict(r) for r in n.usage],
            "window": {d: {k: round(v, 1) for k, v in day.items()} for d, day in dm.window_days.items()},
            "window_runs": {d: [list(r) for r in runs] for d, runs in dm.window_runs.items()},
        }

    def clear_history(self):
        """子端第一次绑定: 自己的历史换成母端的 (今天已用的联网额度保留)."""
        dm, n = self.app.dm, self.app.net
        dm.study_data = {}
        dm.save_study_data()
        dm.focus_sessions = []
        dm.save_focus_log()
        dm.window_days = {}
        dm.window_runs = {}
        dm.save_window_usage(force=True)
        self.app.behavior.clear_history()
        n.usage = []
        n._session = None
        n._usage_dirty = True
        n._save_usage()

    # ---- 状态 ----

    def away_window(self):
        """本机刚从自由学习的外出回来, 还没同步上: (开始毫秒, 结束毫秒). 这段时间本机的记录在同步里有优先权 (MECHANICS 21.6)."""
        a = self.app
        try:
            if not a.sync.peers():
                return None
            return a.behavior.returned(a.sync.my_id, a.sync.synced_since)
        except Exception:
            return None

    def status(self) -> dict:
        from config import RUNNING, ON_BREAK
        a, n = self.app, self.app.net
        st = {"timer": "idle", "net": n.mode, "last_op": int(a.last_op)}
        # 关机 (MECHANICS 15.4): 收到关机指令 / 电量很低时突然离线, 其他设备把本机算作"关机" (效果和在线一样)
        if getattr(a, "power_off", False):
            st["off"] = True
        st["auto"] = bool(getattr(a, "autostart_on", False))      # 开机自启动开着 (MECHANICS 3.4): 关机的前提
        try:
            since = a.sync.adapter_since()                        # 换成了 Adapter: "断网" (MECHANICS 15.5)
        except Exception:
            since = 0
        if since:
            st["adapter"] = int(since)
        bat = getattr(a, "battery", None)
        if bat is not None:
            st["battery"] = int(bat)
        win = self.away_window()
        if win:
            st["away"] = [win[0], win[1]]                         # 刚从自由学习的外出回来: 这次同步有优先权 (MECHANICS 21.6)
        if a.state == RUNNING and a.start_wall:
            st.update(timer="running", since=a.start_wall.isoformat(timespec="seconds"))
        elif a.state == ON_BREAK:
            st.update(timer="break", left=int(a.countdown_seconds))
        if n.mode == "online":
            st["net_left"] = int(n.grant_left)
        if a.prescripts.on_page():
            st["prescript"] = True            # 本机停在指令页上: 空闲词条要所有设备都离开才作废 (MECHANICS 22.7)
        rec = [k for k in ("camera", "screen") if a.recording(k)]   # 本机此刻在录的几路 (合约页同步显示, 18.8)
        if rec:
            st["rec"] = rec
        return st

    # ---- 文档 ----

    def docs(self) -> dict:
        a = self.app
        s = a.settings
        settings = {}
        for key, spec in SCHEMA_BY_KEY.items():
            if key in devsync.LOCAL_SETTINGS:
                continue
            p = s.pending.get(key)
            settings[key] = {
                "value": _norm_setting(key, s.effective.get(key)),
                "pending": None if not p else {
                    "value": _norm_setting(key, p["value"]), "requested_at": p["requested_at"],
                    "apply_at": p["apply_at"].isoformat(timespec="seconds")},
            }
        settings["_setup_until"] = s.setup_until.isoformat(timespec="seconds") if s.setup_until else None
        settings["_free_next"] = sorted(s.free_next)

        # 规则页的名单不同步 (每台设备自己管, 见 MECHANICS 15.2)
        hours = {f"{ds}/{h}": t for ds, day in a.dm.hour_days.items() for h, t in day.items() if t}
        # 细化规范的两个半小时 (MECHANICS 18.4): "日期/小时/1" "日期/小时/2"
        for ds, day in a.dm.hour_halves.items():
            for h, pair in day.items():
                for i, t in enumerate(pair if isinstance(pair, list) else []):
                    if t:
                        hours[f"{ds}/{h}/{i + 1}"] = t

        d = a._bonus_state()
        timer = {"break_carry": int(a.break_carry), "auto_rest": int(a.auto_rest),
                 "bonus": {"week": d.get("bonus_week") or "", "used": int(d.get("bonus_used", 0) or 0),
                           "count": int(d.get("bonus_count", 0) or 0)},
                 # 此刻的专注 / 休息: 哪台设备最后改动就以哪台为准
                 "live": a.timer_live(),
                 # 联网状态 (申请中 / 联网中, MECHANICS 2.8): 同上
                 "net": a.net.live()}
        # 成就: 哪台设备达成都算 (达成时刻取最早的), 见 MECHANICS 17
        # 合约: 进行中的合约以后改的为准, 结束了的合约合并 (MECHANICS 18)
        # 行为模式的外出: 每一项单独比较, 计时 / 统计跟着它走 (MECHANICS 21)
        return {"settings": settings, "hours": hours, "timer": timer, "achievements": a.ach.doc(),
                "contract": a.contract.doc(), "behavior": a.behavior.doc(),
                "prescript": a.prescripts.sync_doc()}

    def apply(self, doc: str, changes: dict):
        a = self.app
        if doc == "settings":
            s = a.settings
            for key, val in changes.items():
                if key == "_setup_until":
                    s.setup_until = s._parse_dt(val) if val else None
                elif key == "_free_next":
                    s.free_next = {k for k in (val or []) if k in SCHEMA_BY_KEY}
                elif key in SCHEMA_BY_KEY and key not in devsync.LOCAL_SETTINGS and isinstance(val, dict):
                    s.effective[key] = s._coerce(key, val.get("value"))
                    p = val.get("pending")
                    at = s._parse_dt(p.get("apply_at")) if isinstance(p, dict) else None
                    if at:
                        s.pending[key] = {"value": s._coerce(key, p.get("value")),
                                          "requested_at": str(p.get("requested_at") or ""),
                                          "apply_at": at}
                    else:
                        s.pending.pop(key, None)
                    s.since[key] = f"{datetime.now():%Y-%m-%d %H:%M}"
            s.save()
            s.apply_to_config()
            a.after_sync_settings(set(changes))
        elif doc == "hours":
            dm = a.dm
            for key, val in changes.items():
                parts = key.split("/")
                if len(parts) == 3 and parts[1].isdigit() and parts[2] in ("1", "2"):
                    ds, h, i = parts[0], parts[1], int(parts[2]) - 1
                    pair = dm.hour_halves.get(ds, {}).get(h)
                    pair = list(pair) if isinstance(pair, list) and len(pair) == 2 else ["", ""]
                    pair[i] = str(val or "")
                    if any(pair):
                        dm.hour_halves.setdefault(ds, {})[h] = pair
                    else:
                        dm.hour_halves.get(ds, {}).pop(h, None)
                    continue
                try:
                    ds, h = key.split("/")
                    int(h)
                except ValueError:
                    continue
                day = dm.hour_days.setdefault(ds, {})
                if val:
                    day[h] = i18n.auto_names_in(str(val))
                else:
                    day.pop(h, None)
            dm.save_hour_log()
        elif doc == "timer":
            if "bonus" in changes and isinstance(changes["bonus"], dict):
                b = changes["bonus"]
                a._timer_extra = {"bonus_week": str(b.get("week") or ""),
                                  "bonus_used": int(b.get("used") or 0),
                                  "bonus_count": int(b.get("count") or 0)}
            if "auto_rest" in changes and changes["auto_rest"] is not None:
                a.auto_rest = max(0, int(changes["auto_rest"]))
            if "break_carry" in changes and changes["break_carry"] is not None:
                a._set_break_carry(int(changes["break_carry"]))
            else:
                a._set_break_carry(a.break_carry)
            if isinstance(changes.get("live"), dict):
                a.adopt_timer(changes["live"])
            if isinstance(changes.get("net"), dict):
                a.adopt_net(changes["net"])
        elif doc == "achievements":
            a.on_achievements_synced(changes)
        elif doc == "contract":
            a.on_contract_synced(changes)
        elif doc == "behavior":
            a.behavior.merge(changes)
            a.after_behavior_synced()
        elif doc == "prescript":
            a.prescripts.sync_apply(changes)

    # ---- 绑定请求 ----

    def pair_request(self, req: dict):
        svc = self.app.sync
        mother = tr("对方") if req["mother"] == req["id"] else tr("本机")
        msg = tr("{name} ({platform}) 请求绑定.", name=req["name"], platform=req["platform"])
        msg += "\n\n" + tr("配对码: {code}", code=req["code"])
        msg += "\n" + tr("母端: {who}", who=mother)
        if req["mother"] == req["id"]:
            msg += "\n\n" + tr("本机的设置, 名单和记录将被对方的数据替换.")
        toast.notice(tr("🔗  绑定请求"), msg, [
            (tr("接受"), lambda t=req["token"]: svc.answer_pairing(t, True), True),
            (tr("拒绝"), lambda t=req["token"]: svc.answer_pairing(t, False), False)])
