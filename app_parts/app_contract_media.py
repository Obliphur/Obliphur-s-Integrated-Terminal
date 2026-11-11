"""合约的录像 / 合并 / 发送 / 违规 / 禁网 (MECHANICS 18.3, 18.6, 18.8).

从 app.py 的 BreakTimerApp 拆出来的 mixin: 状态都在 self 上, 这里只放方法.
检查 / 开始 / 结束在 app_parts/app_contract.py.
"""

import os
import threading
import time
from datetime import datetime, timedelta

import appban
import config
import contract as contract_mod
import devsync
import recorder
import securefile
import toast
import window_guard
from app_parts.app_base import ALARM_WAV, _play_audio_file
from config import ON_BREAK, RUNNING
from i18n import tr


class ContractMediaMixin:
    def _after_contract(self, cid: str, owner: str, kinds):
        """录像结束 (MECHANICS 18.8): 本机是合约主人 → 合并各路并把最终录像同步给所有设备;
        不是主人 → 把本机录的片段 (各路) 自动发给主人合并 (不再逐条询问).
        注意: 这里把正在录的 recorder 交给后台线程去 stop + 等加密, 不能先 _stop_recorder 把它丢掉 ——
        否则合并 / 发送时最后一段 (录屏常常整段就一个, 结束时才加密) 还没落盘就扫不到, 结果就是 0 个."""
        if not cid:
            return
        recs = {}
        for kind in kinds:
            r = self._recorders.pop(kind, None)
            recs[kind] = r
            if r is not None:
                self._log_notice(f"RECORD {kind} stop")
        if owner and owner == self.sync.my_id:
            for kind in kinds:
                self._merge_and_share(cid, kind, recs.get(kind))
        else:
            self._send_segments(cid, owner, kinds, recs)

    def _merge_and_share(self, cid: str, kind: str, rec=None):
        """合约主人: 把本机的 + 收到的片段接成一个视频, 再把最终结果同步给所有设备. 收到新片段时会再调用 (重合并 + 重发).
        rec: 刚结束的这一路 recorder (有的话先 stop + 等最后一段加密, 再扫描片段)."""
        key = (cid, kind)
        if not cid:
            return
        if key in self._merging:
            self._merge_again.add(key)      # 正在合并时又收到片段: 合并完再合并一次 (长的录像合并要好几分钟)
            return
        self._merging.add(key)

        def run():
            try:
                if rec is not None:
                    rec.stop(12.0)                 # 录完当前片段 (Finalize)
                    rec.wait_sealed(12.0)          # 等最后一段加密进数据目录
                if not recorder.files_of(self.dm.data_dir, kind, cid) \
                        and not os.path.isdir(recorder.incoming_dir(self.dm.data_dir, cid, kind)):
                    return
                try:
                    path = recorder.merge(self.dm.data_dir, cid, kind)
                except Exception as e:      # noqa: BLE001
                    self._log_notice(f"RECORD merge {cid}/{kind} failed: {e}")
                    return
                if not path:
                    return
                self._log_notice(f"RECORD merged {cid}/{kind} -> {os.path.basename(path)}")
                self.root.after(0, lambda: self._refresh_if_visible("settings"))
                self._distribute_merged(cid, kind, path)
            finally:
                self._merging.discard(key)
                if key in self._merge_again:
                    self._merge_again.discard(key)
                    self.root.after(0, lambda: self._merge_and_share(cid, kind))
        threading.Thread(target=run, daemon=True, name=f"merge-{cid}-{kind}").start()

    def _distribute_merged(self, cid: str, kind: str, path: str):
        """合约主人: 把合并好的最终录像自动同步给所有在线的绑定设备 (不问)."""
        for s in self.sync.device_states():
            if not s.get("online") or s["id"] == self.sync.my_id:
                continue
            try:
                self.sync.send_file(s["id"], path, cid=cid, kind=kind, final=True)
            except Exception as e:      # noqa: BLE001
                self._log_notice(f"RECORD share {cid}/{kind} to {s['id']} failed: {e}")

    def _send_segments(self, cid: str, owner: str, kinds, recs=None):
        """不是合约主人的设备: 把本机录的片段 (各路) 自动发给主人合并 (MECHANICS 18.8).
        recs: 刚结束的各路 recorder (先 stop + 等加密, 否则最后一段还没落盘就发不出去)."""
        recs = recs or {}

        def go():
            for kind in kinds:                     # 先把本机还在录的收尾 (不管主人在不在线, 片段都要落盘)
                r = recs.get(kind)
                if r is not None:
                    r.stop(12.0)
                    r.wait_sealed(60.0)
            # 记进待发送的清单 (存在数据目录里, 重开 OIT 也还在), 每 30 秒试一次, 全部发成功才划掉
            with self._outbox_lock:
                box = self._outbox_read()
                if not any(e.get("cid") == cid for e in box):
                    box.append({"cid": cid, "owner": owner, "at": time.time()})
                    self._outbox_write(box)
            self._flush_outbox()
        threading.Thread(target=go, daemon=True, name=f"rec-send-{cid}").start()

    def _purge_recordings(self):
        """录像超过 3 天自动清除 (MECHANICS 18.8): 打开时清一次, 之后每小时一次."""
        try:
            busy = [str(getattr(r, "path", "")) for r in self._recorders.values()]
            n = recorder.purge_old(self.dm.data_dir, busy=busy)
            if n:
                self._log_notice(f"RECORD purged {n} file(s) older than {recorder.MAX_AGE_DAYS} days")
                self._on_main(lambda: self._refresh_if_visible("settings"))
        except Exception as e:
            self._log_window(f"ERROR    purge recordings {type(e).__name__}: {e}")

    def _outbox_path(self) -> str:
        return os.path.join(self.dm.data_dir, "rec_outbox.json")

    def _outbox_read(self) -> list:
        d = config.load_json_file(self._outbox_path(), {})
        return [e for e in (d.get("items") or []) if isinstance(e, dict)] if isinstance(d, dict) else []

    def _outbox_write(self, box: list):
        if box:
            config.save_json_file(self._outbox_path(), {"items": box})
        else:
            try:
                securefile.remove(self._outbox_path())
            except OSError:
                pass

    def _flush_outbox(self):
        """待发送清单里的片段发给各自的合约主人 (主人当时不在线 / 传输失败的, 之后再试). 接收端按文件名覆盖, 重发不会重复."""
        if self._outbox_busy:
            return
        self._outbox_busy = True

        def run():
            try:
                with self._outbox_lock:
                    box = self._outbox_read()
                left = []
                for e in box:
                    cid, owner = str(e.get("cid") or ""), str(e.get("owner") or "")
                    if not cid or time.time() - float(e.get("at") or 0) > 7 * 86400:
                        continue                   # 太久了: 放弃 (片段仍在本机)
                    if not self._send_all(cid, owner):
                        left.append(e)
                with self._outbox_lock:
                    # 发送期间又加进来的 (另一个合约) 也留着
                    sent = {e.get("cid") for e in box} - {e.get("cid") for e in left}
                    self._outbox_write([e for e in self._outbox_read() if e.get("cid") not in sent])
            finally:
                self._outbox_busy = False
        threading.Thread(target=run, daemon=True, name="rec-outbox").start()

    def _send_all(self, cid: str, owner: str) -> bool:
        """这个合约本机录的各路片段全部发给主人了吗 (主人不在线 / 不是电脑时返回 False, 等下次)."""
        if any(str(getattr(r, "tag", "")) == cid for r in self._recorders.values()):
            return False                           # 还在录 (同一个合约)
        p = self.sync.peers().get(owner)
        if not p:
            return True                            # 已经解绑了: 不用再发
        online = any(s["id"] == owner and s.get("online") for s in self.sync.device_states())
        # 主人必须是在线的电脑 (手机不能合并); 不是就先留在本机
        if not online or not str(p.get("platform", "")).lower().startswith("win"):
            return False
        done = True
        sent_paths = []
        for kind in recorder.KINDS:
            segs = [s for s in recorder.files_of(self.dm.data_dir, kind, cid) if not s.get("partial")]
            ok = 0
            for s in segs:
                try:
                    r = self.sync.send_file(owner, s["path"], cid=cid, kind=kind)
                    if r == "sent":
                        ok += 1
                        sent_paths.append(s["path"])
                    else:
                        self._log_notice(f"RECORD send {cid}/{kind} {s['name']}: {r}")
                except Exception as e:      # noqa: BLE001
                    self._log_notice(f"RECORD send {cid}/{kind} failed: {e}")
            if ok < len(segs):
                done = False
            if segs:
                self._log_notice(f"RECORD sent {ok}/{len(segs)} {kind} segments for {cid} to {owner}")
        if done and sent_paths:
            # 全部发给主人了: 本机的片段不用再留着 (主人合并后会把最终录像同步回来), 否则它们永远占着地方 (MECHANICS 18.8)
            for p in sent_paths:
                recorder._remove(p)
            self._log_notice(f"RECORD removed {len(sent_paths)} sent segment(s) of {cid}")
        return done

    def recording_offered(self, peer_id: str, who: str, o: dict) -> str:
        """收到别的设备发来的录像 (MECHANICS 18.8): final 是合约主人发来的最终录像 (直接存进 merged 展示);
        否则是片段 (本机是主人, 存进 incoming, 重合并 + 重同步)."""
        cid = str(o.get("cid") or "")
        kind = str(o.get("kind") or "screen")
        if not cid or kind not in recorder.KINDS:
            return "bad-request"
        final = bool(o.get("final"))
        d = recorder.work_dir(self.dm.data_dir, "recv") if final else recorder.incoming_dir(self.dm.data_dir, cid, kind)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            return "busy"
        dest = os.path.join(d, devsync.safe_name(o["name"]))
        n = 1
        # 片段的文件名 (开始时刻 + 合约 id) 是唯一的: 同名的就是同一段 (对方重发), 直接覆盖, 不能合并两次
        while final and os.path.exists(dest):
            stem, ext = os.path.splitext(os.path.basename(o["name"]))
            dest = os.path.join(d, devsync.safe_name(f"{stem}-{n}{ext}"))
            n += 1

        def pull():
            try:
                self.sync.fetch_file(peer_id, o, dest)
            except Exception as e:      # noqa: BLE001
                self._log_notice(f"RECORD recv {cid}/{kind} failed: {e}")
                return
            if final:
                recorder.merged_file(self.dm.data_dir, cid, kind, dest)
                self._log_notice(f"RECORD got merged {cid}/{kind} from {who}")
                self.root.after(0, lambda: self._refresh_if_visible("settings"))
            else:
                self._log_notice(f"RECORD recv {cid}/{kind} segment from {who}")
                # 主人: 收到后重新合并 (稍等一下, 让同一批的几段都到了再合)
                self.root.after(4000, lambda: self._merge_and_share(cid, kind))
        threading.Thread(target=pull, daemon=True, name=f"rec-recv-{cid}").start()
        return ""

    def rec_deleted(self, cid: str, kind: str):
        """别的设备删了某个录像 (MECHANICS 18.8): 本机也删掉对应的合并录像."""
        if recorder.delete_merged(self.dm.data_dir, cid, kind):
            self._log_notice(f"RECORD deleted {cid}/{kind} (synced)")
            self.root.after(0, lambda: self._refresh_if_visible("settings"))

    def delete_recording(self, cid: str, kind: str):
        """用户在 设置 → 录像 删掉一个录像: 本机删, 并通知所有绑定设备也删 (MECHANICS 18.8)."""
        recorder.delete_merged(self.dm.data_dir, cid, kind)
        if self.sync.peers():
            threading.Thread(target=lambda: self.sync.del_recording(cid, kind), daemon=True).start()
        self._refresh_if_visible("settings")

    def _seal_extra(self):
        """记录封锁 (18.4): 完成了一次填写, 另外随机封锁两个可以封锁的格子."""
        c = self.contract
        keys = c.seal_extra(datetime.now(), text_of=self.dm.unit_text, auto=self.dm.auto_text_for_key,
                            fill=config.FILL_WINDOW)
        if not keys:
            return
        self._log_notice(f"CONTRACT seal locks {', '.join(keys)}")
        aw = self.dm.awaiting_slot()
        if aw is not None and self._slot_locked(aw, self.dm.prompt_len):
            self.dm.prompt_slot = None                      # 待填写的那格被封锁了: 不再等它
            self.update_log_badge()
        toast.warn(tr("记录封锁: 另外封锁了 {t} (不能再修改)", t=", ".join(self._cell_label(k) for k in keys)),
                   timeout=10000)
        self._contract_changed()
        if self.sync.peers():
            self.sync.sync_now()

    def _slot_locked(self, slot: datetime, length: int) -> bool:
        """这个记录格被未知变量 / 记录封锁另外封锁了 (填不了)."""
        return self.contract.hour_locked(slot, slot + timedelta(minutes=length), "", datetime.now(),
                                         config.FILL_WINDOW, self.dm.auto_text_for_key)

    @staticmethod
    def _cell_label(key: str) -> str:
        """整点记录格的键 ("日期/小时", 细化规范的半小时 "日期/小时/1") → "10-18 09:00"."""
        parts = key.split("/")
        return f"{parts[0][5:]} {int(parts[1]):02d}:{'30' if parts[2:] == ['2'] else '00'}"

    def _random_effect(self, kind: str, val: str):
        """未知变量的一次判定结果: 抽中的词条 (提示), 或者效果 (休息时间 ±10 分钟由本机改计时器)."""
        c = self.contract
        if kind == "term":
            if val in c.waiting:
                toast.warn(tr("未知变量: 抽中了 {name}, {t} 生效", name=tr(contract_mod.BY_ID[val][3]),
                              t=f"{c.waiting[val][0]:%H:%M}"), timeout=10000, clear=True)
        elif kind in ("rest_cut", "rest_add"):
            delta = contract_mod.RANDOM_REST if kind == "rest_add" else -contract_mod.RANDOM_REST
            self._contract_rest(delta)
            msg = tr("未知变量: 休息时间 +{n} 分钟") if delta > 0 else tr("未知变量: 休息时间 -{n} 分钟")
            toast.warn(msg.format(n=contract_mod.RANDOM_REST // 60), timeout=10000, clear=True)
        elif kind == "lock":
            if val:
                toast.warn(tr("未知变量: 封锁了整点记录 {t} (不能再修改)", t=self._cell_label(val)), timeout=10000, clear=True)
            else:
                toast.info(tr("未知变量: 没有可以封锁的整点记录, 无事发生"), clear=True)
        elif kind == "double":
            toast.info(tr("未知变量: 这次判定作废, 改为判定两次"), clear=True)
        elif kind == "slow":
            toast.warn(tr("未知变量: 下一次专注启用阻滞 (只减慢合约内的时间, 按 {p}% 增长)",
                          p=int(round(contract_mod.RANDOM_SLOW * 100))), timeout=10000, clear=True)
        else:
            toast.info(tr("未知变量: 无事发生"), clear=True)

    def _contract_rest(self, delta: int):
        """未知变量: 休息时间 +/- delta 秒. 正在休息时改这次休息 (先减合约中得到的, 合约开始前的放在最后),
        否则改积攒的休息时间."""
        self._mine()
        c = self.contract
        if self.state == ON_BREAK:
            left = max(0, int(self.countdown_seconds))
            new = max(0, left + int(delta))
            if new < left:
                pre_in = min(left, int(self._break_pre))
                lost = max(0, (left - new) - (left - pre_in))
                self._break_pre = max(0.0, float(self._break_pre) - lost)
            self.countdown_seconds = new
            if self.break_end_wall is not None:
                self.break_end_wall += timedelta(seconds=new - left)
            self._save_timer_state()
        else:
            self._set_break_carry(self.break_carry + int(delta))
            c.pre_cap(self.break_carry)
        self.refresh_display()

    def close_random_term(self, tid: str):
        """未知变量选中的词条: 点击关闭, 算一次违反."""
        c = self.contract
        if not c.active or tid not in c.rand:
            return
        name = tr(contract_mod.BY_ID[tid][3])
        if not toast.ask(tr("关闭未知变量选中的 {name}?", name=name) + "\n" + tr("会算作一次违反."),
                         title=tr("关闭词条"), danger=True):
            return
        self.contract_violation(tr("关闭了未知变量选中的 {name}", name=name), tid, drop=True)
        self._contract_changed()

    def contract_violation(self, why: str, tid: str, drop: bool = False):
        """违反了词条 tid (软规则): 响警报, 记一次, 这个词条降一级 (drop: 直接去掉); 到上限就结束合约."""
        c = self.contract
        if c.active and not self.dm.resetting and c.echo(tid):
            # 已经失效的词条再次触发: 照常响警报 (每个词条 2 分钟最多一次), 不扣目标生命值
            if c.echo_due(tid):
                self._play_alarm()
                self._log_notice(f"CONTRACT echo {tid}  {why}")
                toast.warn(tr("⚠ {why} · {name} 已失效, 不扣目标生命值", why=why,
                              name=tr(contract_mod.BY_ID[tid][3])), timeout=10000)
            return
        if not c.active or self.dm.resetting or (tid and not c.has(tid)):
            return
        self._play_alarm()
        old = tr(contract_mod.BY_ID[tid][3]) if tid else ""
        over, new = c.violate(tid, drop)
        if not tid:
            after = tr("违反一次")
        elif not new:
            after = tr("{a} 失效", a=old)
        elif new == tid:
            after = tr("{a} 降一级", a=old)
        else:
            after = tr("{a} 降为 {b}", a=old, b=tr(contract_mod.BY_ID[new][3]))
        self._log_notice(f"CONTRACT violation {c.violations}/{c.violation_limit}: {tid or '-'} -> {new or '-'}  {why}")
        msg = (tr("⚠ 违反合约: {why} · 目标生命值 {n} / {m}", why=why, n=c.life, m=c.violation_limit)
               + "\n" + after + "  ·  " + tr("危机等级 {lv}", lv=c.level))
        toast.error(msg)
        self.dm.add_violation(msg)               # 违规记录页 (MECHANICS 8.1): 内容照搬这段提示
        self._joint_hp_lost(msg)                 # 联合行动: 对方在下一次开始休息 / 合约结束时知道
        if over:
            self.end_contract("failed")
        else:
            self._refresh_if_visible("contract")
            if self.sync.peers():
                self.sync.sync_now()

    def _contract_hours(self):
        """整点记录 (MECHANICS 18.4): 细化规范的半小时补记; 合约中过了填写时限还没填写算一次违反 (由合约的主人判定)."""
        c = self.contract
        if not self.dm.hour_enabled:
            return
        now = datetime.now()
        units = c.hour_units(now)
        self.dm.finalize_halves(units, now)
        if self.state == ON_BREAK and c.mark_rest_cells(now, config.FILL_WINDOW, self.dm.unit_text,
                                                         self.dm.auto_text_for_key):
            if self.current_page == "log":
                self.pages["log"].refresh()
        # 记录封锁: 合约里填写了的整小时格记下来, 合约结束后照样锁着 (18.4)
        if c.seal_filled(now, self.dm.unit_text, self.dm.auto_text_for_key):
            self._refresh_if_visible("log")
            if self.sync.peers():
                self.sync.sync_now()
        if not self._owner_away(c.owner):
            return
        for key in c.hour_misses(now, config.FILL_WINDOW, self.dm.unit_text,
                                 self.dm.auto_text_for_key):
            parts = key.split("/")
            label = f"{int(parts[1]):02d}:{'30' if parts[2:] == ['2'] else '00'}"   # 写这一格的起点 (同整点记录页)
            self.contract_violation(tr("整点记录 {t} 超时未填写", t=label), "")
            if not c.active:
                return

    def _check_supply(self):
        """支援短缺: 休息用到合约开始前积攒的休息时间之前 3 / 1 分钟各提醒一次; 用超了算一次违反."""
        c = self.contract
        if self.state != ON_BREAK or not c.supply_watch() or c.live.get("id") != self._break_cid:
            return
        left = max(0, int(self.countdown_seconds))
        until = c.supply_until(left, self._break_pre)
        for sec in contract_mod.SUPPLY_WARN:
            key = (self._break_cid, round(c.supply_limit or 0), sec)
            if until is not None and 0 < until <= sec and key not in self._supply_warned:
                self._supply_warned.add(key)
                toast.warn(tr("支援短缺: {n} 分钟后将超出可用的合约开始前休息时间", n=sec // 60), timeout=10000, clear=True)
        if self._owner_away(c.owner):
            tid = c.supply_check(left, self._break_pre)
            if tid:
                self.contract_violation(tr("用超了合约开始前积攒的休息时间"), tid)

    def on_contract_synced(self, changes: dict):
        self.contract.merge(changes)
        self._check_contract()

    def _apply_net_bans(self):
        """乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (MECHANICS 18.3): 这些软件禁止联网 (打开它们不算违反).
        每秒: 过滤代理按 exe 名拦; 每 5 秒: 给正在运行的这些程序加 / 删防火墙出站规则 (不走系统代理的也拦住)."""
        c = self.contract
        on = c.active and not self.dm.resetting and not config.dev_mode_enabled()
        banned = c.banned_exes() if on else set()
        try:
            self.net.proxy.rules.banned = banned
            self.net.proxy.rules.banned_domains = c.banned_domains() if on else set()   # 这一类的网站也封锁
        except Exception:
            pass
        if (banned or self.appban.paths) and not self._appban_busy and time.monotonic() - self._appban_at >= 5:
            self._appban_at = time.monotonic()
            self._appban_busy = True
            threading.Thread(target=self._appban_sync, args=(banned,), daemon=True).start()

    def _appban_sync(self, banned):
        try:
            if banned:
                self.appban.sync(appban.process_paths(banned))
            else:
                self.appban.clear_all()
        except Exception as e:
            self._log_window(f"ERROR    appban {type(e).__name__}: {e}")
        finally:
            self._appban_busy = False

    def lift_net_ban(self, tid: str):
        """合约页上点击乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒: 解除这一类软件的网络禁令,
        扣掉词条等级那么多目标生命值 (MECHANICS 18.3). 先确认."""
        c = self.contract
        if not c.can_lift(tid):
            return
        name = tr(contract_mod.BY_ID[tid][3])
        n = c.lift_cost(tid)
        msg = tr("解除「{name}」的网络禁令?", name=name) + "\n" + \
            tr("将扣除目标生命值 {n} (现在 {life} / {m}).", n=n, life=c.life, m=c.violation_limit)
        if n >= c.life:
            msg += "\n" + tr("目标生命值会归零, 合约失败.")
        if not toast.ask(msg, title=tr("解除网络禁令"), ok_text=tr("解除"), danger=True):
            return
        if not c.can_lift(tid):
            return
        over, n = c.lift_ban(tid)
        self._log_notice(f"CONTRACT lift ban {tid} -{n}: {c.violations}/{c.violation_limit}")
        text = (tr("解除了「{name}」的网络禁令 · 目标生命值 -{n} ({life} / {m})", name=name, n=n,
                   life=c.life, m=c.violation_limit) + "\n" + tr("危机等级 {lv}", lv=c.level))
        toast.warn(text)
        self.dm.add_violation(text)              # 违规记录页 (MECHANICS 8.1): 目标生命值的变化都记下来
        self._joint_hp_lost(text)                # 联合行动: 对方在下一次开始休息 / 合约结束时知道
        self._apply_net_bans()
        if over:
            self.end_contract("failed")
            return
        self._contract_changed()
        if self.sync.peers():
            self.sync.sync_now()

    # ---- 合约 · 录像 (目光 / 刻痕, MECHANICS 18.3) ----

    def _start_recorder(self, kind: str):
        r = recorder.Recorder(self.dm.data_dir, kind, tag=str(self.contract.live.get("id") or ""),
                              camera=self._local_camera_id() if kind == "camera" else "",
                              on_file=lambda path, k=kind: self._log_notice(f"RECORD {k} saved {os.path.basename(path)}"))
        self._recorders[kind] = r
        r.start()
        self._log_notice(f"RECORD {kind} start")
        return r

    def _warn_pick_term(self, tid: str) -> bool:
        """选中录像词条 (目光 / 刻痕) 前警告一次: 会录下什么, 加密只能在 OIT 里看, 刻痕会在各设备间传输合并.
        选不选是用户自己的决定 (MECHANICS 18.8). 返回要不要选上."""
        if tid == "gaze":
            return toast.ask(
                tr("该词条会使用摄像头记录你的行为(每30秒拍一张照片,合约结束后合成视频),但照片和视频会被加密,只能在OIT终端查看,防止自动上传类似的功能泄露隐私."),
                title=tr("主管!"), ok_text=tr("确定"), cancel_text=tr("取消"), danger=True)
        if tid == "scar":
            return toast.ask(
                tr("该词条会使用截图记录你的行为(每30秒截一张图,合约结束后合成视频),但截图和视频会被加密,只能在OIT终端查看,防止自动上传类似的功能泄露隐私."),
                title=tr("主管!"), ok_text=tr("确定"), cancel_text=tr("取消"), danger=True)
        return True

    def _scar_focus(self) -> str:
        """刻痕的焦点设备 (MECHANICS 18.8): 哪台设备有操作就录哪台的屏幕, 每 20 秒判定一次;
        20 秒内两台都有操作时优先录电脑. 都没有操作时保持上一个焦点设备. 返回焦点设备的 id."""
        mono = time.monotonic()
        if self._scar_focus_id and mono - self._scar_focus_at < recorder.FOCUS_EVERY:
            return self._scar_focus_id
        self._scar_focus_at = mono
        nowms = time.time() * 1000
        win = recorder.FOCUS_EVERY * 1000
        cands = [(True, float(self.last_op), self.sync.my_id)]        # 本机 (电脑)
        plat = self.sync.peers()
        stat = {s["id"]: s for s in self.sync.peer_status()}
        for s in self.sync.device_states():
            if not s.get("online"):
                continue
            did = s["id"]
            is_pc = str((plat.get(did) or {}).get("platform", "")).lower().startswith("win")
            cands.append((is_pc, float((stat.get(did) or {}).get("last_op", 0)), did))
        active = [c for c in cands if nowms - c[1] <= win]
        if not active:
            if self._scar_focus_id in {c[2] for c in cands}:
                return self._scar_focus_id                           # 都没操作: 保持上一个焦点设备
            active = cands
        if any(c[0] for c in active):
            active = [c for c in active if c[0]]                      # 两台都有操作: 优先电脑
        self._scar_focus_id = max(active, key=lambda c: c[1])[2]
        return self._scar_focus_id

    def _stop_recorder(self, kind: str):
        r = self._recorders.pop(kind, None)
        if r is not None:
            threading.Thread(target=r.stop, daemon=True).start()     # Finalize 可能要一两秒, 不卡界面
            self._log_notice(f"RECORD {kind} stop")

    def _stop_all_recorders(self, wait: float = 0.0):
        recs = list(self._recorders.values())
        self._recorders.clear()
        for r in recs:
            r.stop(0)
        for r in recs:
            r.stop(wait)
            if wait:
                r.wait_sealed(wait)

    def recording(self, kind: str) -> bool:
        """本机此刻在录这一路吗 (设置 → 录像: 正在写的文件不能删)."""
        r = self._recorders.get(kind)
        return r is not None and r.alive

    def _rec_text(self) -> str:
        """合约页: 录像的状态 —— 各设备在录哪一路也同步显示 (MECHANICS 18.8)."""
        out = []
        cid = self.contract.live.get("id")
        # 别的设备此刻在录的几路 (同步来的状态): kind -> [设备名]
        peer_rec = {}
        try:
            online = {s["id"] for s in self.sync.device_states() if s.get("online")}
            for s in self.sync.peer_status():
                if s.get("id") not in online:
                    continue
                for k in (s.get("rec") or []):
                    peer_rec.setdefault(k, []).append(str(s.get("name") or "?"))
        except Exception:      # noqa: BLE001
            pass
        for kind, label in (("camera", tr("📷 摄像头")), ("screen", tr("🖥 屏幕"))):
            if kind not in self.contract.records():
                continue
            who = []
            if self.recording(kind):
                who.append(tr("本机"))
            who += peer_rec.get(kind, [])
            if who:
                out.append(label + " " + tr("{who} 录像中", who="、".join(who)))
                continue
            r = self._recorders.get(kind)
            skip = self._rec_skip.get(kind)
            if r is not None:
                out.append(label + " " + tr("正在开始录像…"))
            elif skip and skip[0] == cid and skip[1] == "none":
                out.append(label + " " + tr("本机没有摄像头, 不录"))
            elif skip and skip[0] == cid and skip[1] == "ask":
                out.append(label + " " + tr("等待确认录像"))
            else:
                out.append(label + " " + tr("未录像"))
        return "   ·   ".join(out)

    def _sync_recorders(self):
        """每秒: 合约要录的 (摄像头 / 屏幕) 在录, 不要录的停下. 摄像头录着录着没了 (被关掉 / 拔掉 / 被禁用),
        或者打不开, 算违反目光 (本机没有摄像头时不录, 不算). 录屏出错不算, 过一会儿再试."""
        if self._rec_pending:
            return
        c = self.contract
        want = set(c.records(self.sync.my_id)) if c.active and not self.dm.resetting else set()
        # 刻痕: 只有焦点设备录屏 (哪台有操作录哪台, 每 20 秒判定一次; 两台都有操作时录电脑, MECHANICS 18.8)
        if "screen" in want and self._scar_focus() != self.sync.my_id:
            want.discard("screen")
        # 只在专注期间录像 (设置 → 录像, 默认开启): 休息 / 空闲时停下 (不算违反), 再开始专注时另起一段, 最后拼接成一个视频
        if self.settings.value("REC_FOCUS_ONLY") and self.state != RUNNING:
            want = set()
        cid = c.live.get("id")
        for kind in recorder.KINDS:
            r = self._recorders.get(kind)
            if kind not in want:
                if r is not None:
                    self._stop_recorder(kind)
                continue
            skip = self._rec_skip.get(kind)
            if skip and skip[0] == cid:
                continue
            if r is None:
                if time.monotonic() < self._rec_retry.get(kind, 0):
                    continue
                if cid != self._rec_started_cid and self.settings.value("REC_ASK") and (kind, cid) not in self._rec_ok:
                    self._ask_recording(kind, cid)
                    continue
                self._start_recorder(kind)
                continue
            if r.state == "failed" and kind == "camera" and r.error == "no camera":
                self._recorders.pop(kind, None)
                self._rec_skip[kind] = (cid, "none")
            elif kind == "camera" and (r.state in ("failed", "lost", "stopped") or r.stalled()):
                err = r.error
                self._stop_recorder(kind)
                self._log_notice(f"RECORD camera lost: {err}")
                self.contract_violation(tr("摄像头被关闭 / 打不开"), "gaze")
            elif r.state in ("failed", "lost", "stopped"):
                self._log_notice(f"RECORD {kind} {r.state}: {r.error}")
                self._recorders.pop(kind, None)
                self._rec_retry[kind] = time.monotonic() + 30

    def _ask_recording(self, kind: str, cid):
        """别的设备开始的合约要录像, 设置里开着"录像前询问": 问一次. 拒绝算一次违反, 这个词条失效;
        ✕ 关掉: 2 分钟后再问."""
        self._rec_skip[kind] = (cid, "ask")
        what = tr("用摄像头录像") if kind == "camera" else tr("录制整个屏幕")
        tid = "gaze" if kind == "camera" else "scar"

        def yes():
            self._rec_ok.add((kind, cid))          # 这次合约答应过了: 只在专注期间录像时, 之后每次开始专注不再问
            if self._rec_skip.get(kind) == (cid, "ask"):
                self._rec_skip.pop(kind, None)
            if self.contract.live.get("id") == cid and kind in self.contract.records() and kind not in self._recorders:
                self._start_recorder(kind)

        def no():
            self._rec_skip[kind] = (cid, "declined")
            if self.contract.live.get("id") == cid:
                self.contract_violation(tr("拒绝了录像"), tid)

        def closed(_opts=None):
            if self._rec_skip.get(kind) == (cid, "ask"):
                self._rec_skip.pop(kind, None)
                self._rec_retry[kind] = time.monotonic() + 120

        dlg = toast.notice(
            tr("⚔ 合约要求录像"),
            tr("进行中的合约要求本机{what} ({term}).", what=what, term=tr(contract_mod.BY_ID[tid][3])),
            buttons=[(tr("开始录像"), yes, True), (tr("拒绝"), no, False)])
        try:
            dlg.bind("<Destroy>", lambda e: e.widget is dlg and self.root.after(100, closed), add="+")
        except Exception:
            pass

    def terminate_recording(self):
        """终止整个录制过程 (MECHANICS 18.8): 任意一端都能终止 —— 正在录的每一路各算一次违反 (对应词条失效),
        停掉本机的录像并通知所有设备也停 (违反随合约同步). 已经录下的照样保存."""
        terms = [t for t, k in contract_mod.RECORD.items() if self.contract.has(t) and k in self.contract.records()]
        if not terms:
            return
        if not toast.ask(
                tr("终止整个录制过程?"),
                title=tr("■ 终止录像"), danger=True):
            return
        cid = str(self.contract.live.get("id") or "")
        for t in terms:
            if self.contract.has(t):
                self._stop_recorder(contract_mod.RECORD[t])
                self.contract_violation(tr("终止了录像"), t)
        self.rec_stop(cid)
        if self.sync.peers():
            threading.Thread(target=lambda: self.sync.stop_recording(cid), daemon=True).start()
            self.sync.sync_now()

    def rec_stop(self, cid: str):
        """别的设备 (或本机) 终止了录制 (MECHANICS 18.8): 停掉本机正在录的各路, 这个合约不再自动重开 (违反随合约同步)."""
        cur = str(self.contract.live.get("id") or "")
        if cid and cur and cid != cur:
            return
        for kind in recorder.KINDS:
            if kind in self._recorders:
                self._stop_recorder(kind)
            self._rec_skip[kind] = (cur, "stopped")

    def _record_lines(self, terms) -> list:
        kinds = [contract_mod.RECORD[t] for t in terms if t in contract_mod.RECORD]
        out = []
        if "camera" in kinds:
            out.append(tr("📷 合约期间用摄像头录像"))
        if "screen" in kinds:
            out.append(tr("🖥 合约期间录制整个屏幕"))
        return out

    def _check_contract_windows(self, ws):
        """通道阻塞: 专注中任务栏上的窗口 (开着超过 2 分钟的, ws) 超过词条的上限 (不关窗口, 只算违反; 越过的那一下算一次).
        同一个程序的多个窗口算一个, 浏览器的每个窗口各算一个."""
        c = self.contract
        _tid, lim = c.window_watch()                 # 已经失效的按失效前的上限 (再次超过只响警报)
        if not lim or self.dm.resetting:
            return
        if self.state != RUNNING:                    # 只在专注时段算, 离开专注重新计
            c.window_reset()
            return
        if ws is None:
            return
        tid = c.window_check(window_guard.channel_count(ws, contract_mod.BROWSER_EXES))
        if tid:
            self.contract_violation(tr("窗口超过 {n} 个", n=lim), tid)

    def _contract_quick(self):
        """抑制: 用了快速访问."""
        tid = self.contract.quick_check()
        if tid:
            self.contract_violation(tr("使用了快速访问"), tid)

    def _play_alarm(self):
        """违反合约的警报 (sounds/target_alarm.wav); 放不出来时蜂鸣."""
        def run():
            if _play_audio_file(ALARM_WAV):
                return
            try:
                import winsound
                for _ in range(3):
                    winsound.Beep(1400, 140)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _sync_bonus_button(self):
        """合约中 / 外出中 / 本周已经领完时隐藏"我累了"按钮 (下周一再出现)."""
        if not hasattr(self, "lbl_bonus"):
            return
        if self.contract.active or self.behavior.active() is not None or self._bonus_left_this_week() <= 0:
            if self.lbl_bonus.winfo_manager() == "place":
                self.lbl_bonus.place_forget()
        elif self.lbl_bonus.winfo_manager() != "place":
            self.lbl_bonus.place(relx=1.0, x=-28, y=22, anchor="ne")
            self.lbl_bonus.lift()
