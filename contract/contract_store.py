"""合约存档与设备同步: contract.json 的读写、doc / merge、时间重叠的两个合约 (MECHANICS 15.5, 18)。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

from config import load_json_file, save_json_file

from contract.contract_rules import (
    HISTORY_KEEP, BY_ID, combo_level, clean_terms, _parse,
)


class StoreMixin:
    def load(self):
        d = load_json_file(self.path, {})
        d = d if isinstance(d, dict) else {}
        live = d.get("live")
        self.live = live if isinstance(live, dict) and live.get("state") in ("active", "none") \
            else {"state": "none"}
        if self.active:
            self.live["terms"] = clean_terms(self.live.get("terms"))
        self.history = [h for h in (d.get("history") or []) if isinstance(h, dict) and h.get("start")]
        self.draft = clean_terms(d.get("draft"))
        self.voided = {str(x) for x in (d.get("void") or []) if x}
        self.conflicts = [(a, b) for a, b in (d.get("conflicts") or []) if isinstance(a, dict) and isinstance(b, dict)]

    def save(self):
        if self.blocked():
            return
        self.history = sorted(self.history, key=lambda h: str(h.get("end")))[-HISTORY_KEEP:]
        save_json_file(self.path, {"live": self.live, "history": self.history, "draft": self.draft,
                                   "void": sorted(self.voided), "conflicts": [list(c) for c in self.conflicts]})

    def doc(self) -> dict:
        out = {"live": dict(self.live), "history": [dict(h) for h in self.history]}
        for vid in self.voided:
            out["void:" + vid] = 1                # 每个作废的 id 单独一项: 同步时只会增加
        return out

    # ---- 时间重叠的两个合约 (例如一台设备用 Adapter 断网期间各自开始了合约, MECHANICS 15.5) ----

    @staticmethod
    def _brief(rec: dict, live: bool) -> dict:
        """冲突里一个合约的摘要: id, 起止, 危机等级, 词条, 结果 ("" = 进行中); 进行中的带上整个 live (保留它时恢复)."""
        terms = list(rec.get("terms") or [])
        rand = set(rec.get("rand") or [])
        mine = [t for t in terms if t in BY_ID and t not in rand]
        out = {"id": str(rec.get("id")), "start": rec.get("start"), "end": rec.get("end"),
               "level": int(rec.get("level") or (sum(BY_ID[t][2] for t in mine) + combo_level(mine))),
               "terms": terms, "result": "" if live else str(rec.get("result") or "")}
        if live:
            out["live"] = dict(rec)
        return out

    @staticmethod
    def _overlap(a: dict, b: dict) -> bool:
        sa, ea, sb, eb = _parse(a.get("start")), _parse(a.get("end")), _parse(b.get("start")), _parse(b.get("end"))
        return bool(sa and ea and sb and eb and sa < eb and sb < ea)

    def _add_conflict(self, mine: dict, theirs: dict):
        ids = {mine["id"], theirs["id"]}
        if mine["id"] == theirs["id"] or ids & self.voided:
            return
        if any({a["id"], b["id"]} == ids for a, b in self.conflicts):
            return
        self.conflicts.append((mine, theirs))

    def resolve_conflict(self, keep_id: str) -> bool:
        """用户选了留下 keep_id: 另一个作废 (进行中的直接结束, 不给奖励; 记录里删掉), 留下的要是被盖掉的进行中合约就恢复它.
        已经给出的休息时间不收回. 返回有没有处理."""
        keep_id = str(keep_id)
        hit = next((c for c in self.conflicts if keep_id in (c[0]["id"], c[1]["id"])), None)
        if hit is None:
            return False
        keep = hit[0] if hit[0]["id"] == keep_id else hit[1]
        drop = hit[1] if keep is hit[0] else hit[0]
        self.voided.add(drop["id"])
        self._apply_voids()
        if keep.get("live") and not self.active and keep_id not in {str(h.get("id")) for h in self.history}:
            self.live = dict(keep["live"])        # 留下的是被同步盖掉的那个进行中合约: 恢复 (之后随同步)
            self.live["terms"] = clean_terms(self.live.get("terms"))
        self.save()
        return True

    @staticmethod
    def _keep_counts(mine: dict, new: dict) -> dict:
        """同一个合约的进行中状态整个按"后改的"覆盖: 别的设备几乎同时改了别的东西时, 本机刚记下的违反 (例如整点记录超时)
        会被盖掉, 目标生命值就像没扣. 违反次数只增不减, 算过的记录格 / 另外封锁的格子只增不删: 取两边的并."""
        if not (isinstance(mine, dict) and mine.get("state") == "active" and new.get("state") == "active"
                and str(mine.get("id")) == str(new.get("id"))):
            return new
        out = dict(new)
        out["violations"] = max(int(mine.get("violations") or 0), int(new.get("violations") or 0))
        out["hl"] = sorted(set(new.get("hl") or []) | set(mine.get("hl") or []))
        rest_h = sorted(set(new.get("rest_h") or []) | set(mine.get("rest_h") or []))
        if rest_h:
            out["rest_h"] = rest_h
        fine_h = sorted(set(new.get("fine_h") or []) | set(mine.get("fine_h") or []))
        if fine_h:
            out["fine_h"] = fine_h
        seals = [list(v) for v in new.get("seals") or []]
        seals += [list(v) for v in mine.get("seals") or [] if list(v) not in seals]
        if seals:
            out["seals"] = seals
        tally = StoreMixin._keep_tally(mine.get("tally"), new.get("tally"))
        if tally:
            out["tally"] = tally
        return out

    @staticmethod
    def _keep_tally(a, b) -> dict:
        """计数 (_tally) 的合并: 同一个钟点每台设备取大的, 用掉的取大的; 不同钟点取后面的."""
        a = a if isinstance(a, dict) else {}
        b = b if isinstance(b, dict) else {}
        out = {}
        for kind in set(a) | set(b):
            x, y = a.get(kind), b.get(kind)
            x, y = (x if isinstance(x, dict) else None), (y if isinstance(y, dict) else None)
            if not x or not y or str(x.get("h")) != str(y.get("h")):
                out[kind] = max((v for v in (x, y) if v), key=lambda v: str(v.get("h")))
                continue
            n = dict(x.get("n") or {})
            for dev, v in (y.get("n") or {}).items():
                n[dev] = max(int(n.get(dev) or 0), int(v or 0))
            out[kind] = {"h": x.get("h"), "n": n, "used": max(int(x.get("used") or 0), int(y.get("used") or 0))}
        return out

    def _apply_voids(self) -> bool:
        """作废的合约: 进行中的结束 (不给奖励, 不留记录), 记录里删掉, 相关的冲突不用再问. 返回进行中的合约变了没有."""
        changed = False
        if self.active and str(self.live.get("id")) in self.voided:
            self.live = {"state": "none", "owner": self.live.get("owner") or ""}
            self._watch = {}
            changed = True
        self.history = [h for h in self.history if str(h.get("id")) not in self.voided]
        self.conflicts = [(a, b) for a, b in self.conflicts if not ({a["id"], b["id"]} & self.voided)]
        return changed

    def merge(self, changes: dict) -> bool:
        """同步来的改动. 进行中的合约: 后改的覆盖先改的; 记录: 合并 (按 id). 返回进行中的合约是否变了.
        两个不同的合约时间重叠 (进行中的被另一个盖掉, 或者送来的记录和本机的重叠): 记下冲突, 让用户选留哪个 (15.5)."""
        changed = False
        for key in changes:
            if str(key).startswith("void:") and changes[key]:
                self.voided.add(str(key)[5:])
        mine_live = self._brief(self.live, True) if self.active else None
        ended = {str(h.get("id")) for h in (changes.get("history") or []) if isinstance(h, dict)}
        ended |= {str(h.get("id")) for h in self.history}
        if mine_live and mine_live["id"] in ended:
            mine_live = None                         # 本机这个合约在别的设备上已经结束了: 正常切换, 不算冲突
        if isinstance(changes.get("live"), dict) and changes["live"].get("state") in ("active", "none"):
            new = changes["live"]
            if str(new.get("id")) in self.voided and new.get("state") == "active":
                new = None                           # 已经作废的合约又被送回来: 不收
            if new is not None and new != self.live:
                if mine_live and new.get("state") == "active" and str(new.get("id")) != mine_live["id"]:
                    self._add_conflict(mine_live, self._brief(new, True))
                new = self._keep_counts(self.live, new)
                self.live = dict(new)
                if self.active:
                    self.live["terms"] = clean_terms(self.live.get("terms"))
                changed = True
        if isinstance(changes.get("history"), list):
            have = {str(h.get("id")) for h in self.history}
            local = [self._brief(h, False) for h in self.history] + ([mine_live] if mine_live else [])
            for h in changes["history"]:
                hid = str(h.get("id")) if isinstance(h, dict) else ""
                if isinstance(h, dict) and h.get("start") and hid not in have and hid not in self.voided:
                    for loc in local:
                        if loc["id"] != hid and self._overlap(loc, h):
                            self._add_conflict(loc, self._brief(h, False))
                    self.history.append(dict(h))
                    have.add(hid)
        if self._apply_voids():
            changed = True
        self.save()
        return changed
