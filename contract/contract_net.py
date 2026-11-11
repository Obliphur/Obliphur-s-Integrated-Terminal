"""合约的网络禁令: 乐理之灾 / 无言为真 / 禁游令 / 低帧的诅咒 (MECHANICS 18.3)。

从 contract.py 拆出来的 mixin; 依赖方向: contract.py 导入它们, 它们从不导入 contract.py.
"""

from contract.contract_rules import (
    LOWER, SINGLE, SUPPLY, RECORD, BANS, NET_BAN_EXES, BAN_DOMAINS, clean_terms,
)


class NetMixin:
    def net_bans(self) -> list:
        """现在禁止联网的几类 (BANS 之一; 未知变量选中的也算)."""
        return [k for k in BANS if self.has(k)] if self.active else []

    def banned_exes(self) -> set:
        """现在禁止联网的程序 (exe 名, 小写)."""
        out = set()
        for k in self.net_bans():
            out |= set(NET_BAN_EXES.get(k, ()))
        return out

    def banned_domains(self) -> set:
        """现在封锁的网站 (域名, 子域名也算)."""
        out = set()
        for k in self.net_bans():
            out |= set(BAN_DOMAINS.get(k, ()))
        return out

    def can_lift(self, tid: str) -> bool:
        """点击词条可以解除网络禁令: 开始时选的 (未知变量选中的照旧是点击关闭)."""
        return self.active and tid in BANS and self.has(tid) and tid not in self.rand

    def lift_cost(self, tid: str) -> int:
        """解除网络禁令扣多少目标生命值: 词条现在的等级."""
        return max(1, self.term_level(tid))

    def lift_ban(self, tid: str):
        """解除网络禁令: 扣掉 lift_cost 点目标生命值, 去掉这个词条 (组合里算成员失效; 不算违反, 也不会再响警报).
        返回 (是否达到了上限, 扣了几点); 不能解除时 (False, 0)."""
        if not self.can_lift(tid):
            return False, 0
        n = self.lift_cost(tid)
        self.downgrade(tid, drop=True)
        self.live["violations"] = self.violations + n
        self.save()
        return self.violations >= self.violation_limit, n

    def violate(self, tid: str, drop: bool = False):
        """违反了一次词条 tid: 记一次, 这个词条降一级 (drop: 直接去掉, 关闭未知变量选中的词条).
        返回 (是否达到了上限, 降级后的词条 / "" = 失效). tid = "": 不是哪个词条的违反 (整点记录超时未填写), 只记一次."""
        if not self.active:
            return False, ""
        new = self.downgrade(tid, drop) if tid else ""
        self.live["violations"] = self.violations + 1
        self.save()
        return self.violations >= self.violation_limit, new

    def downgrade(self, tid: str, drop: bool = False) -> str:
        """词条降一级: 系列词条换成低一档的, 只有一档的等级减一; 降到底就失效 (drop: 直接去掉).
        返回降级后的词条 ("" = 失效)."""
        terms = self.terms
        if tid not in terms:
            return ""
        old_share = self.net_share()
        old_supply = self._supply_share(terms)
        cut = dict(self.cut)
        rand = self.rand
        i = terms.index(tid)
        if drop:
            terms.pop(i)
            cut.pop(tid, None)
            new = ""
        elif tid in LOWER:
            terms[i] = new = LOWER[tid]
        elif tid in SINGLE and self.term_level(tid) > 1:
            cut[tid] = int(cut.get(tid) or 0) + 1
            new = tid
        else:
            terms.pop(i)
            cut.pop(tid, None)
            new = ""
        self.live["terms"] = clean_terms(terms)
        self.live["cut"] = cut
        gone = not new and not drop and tid not in rand and tid not in RECORD
        if gone and tid not in self.lost:    # 失效了照样检查, 再次触发只响警报
            self.live["lost"] = self.lost + [tid]
        if not new and tid in rand:          # 未知变量选中的词条因为违反 (或关闭) 失效: 可以再回到词条池一次
            viol = list(self.live.get("rand_viol") or [])
            if tid not in viol:
                self.live["rand_viol"] = viol + [tid]
        if tid in rand:                      # 未知变量选中的词条降级后还是它选中的 (移除的时刻不变)
            rand = [new if t == tid else t for t in rand if new or t != tid]
            self.live["rand"] = rand
            till = dict(self.live.get("rand_until") or {})
            at = till.pop(tid, None)
            if new and at is not None:
                till[new] = at
            self.live["rand_until"] = till
        # 信息阻断: 降一级 = 多给低一档多出来的那部分额度 (从现在用到的地方算起)
        n = self.live.get("net")
        new_share = self.net_share()
        if isinstance(n, dict) and old_share is not None and new_share is not None:
            n["limit"] = float(n.get("limit") or 0) + float(n.get("base") or 0) * (new_share - old_share)
        # 支援短缺: 同样多给低一档多出来的那部分; 失效了就不再限制
        new_supply = self._supply_share(self.live["terms"])
        if new_supply is None:
            if gone and tid in SUPPLY:       # 失效时的上限留着, 再次用超时响警报
                self.live["supply_lost"] = self.live.get("supply")
            self.live["supply"] = None
        elif old_supply is not None and self.live.get("supply") is not None:
            self.live["supply"] = round(float(self.live["supply"]) + self.pre * (new_supply - old_supply), 1)
        self.save()
        return new
