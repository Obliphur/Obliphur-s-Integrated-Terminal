"""X 词条的记录输入框: 自动填写、提交、放弃 (MECHANICS 22.8)。

从 prescript.py 拆出来的 mixin; 依赖方向: prescript.py 导入它们, 它们从不导入 prescript.py.
"""

from datetime import datetime

from i18n import tr

from prescript.prescript_common import (
    FIXED_T, LOG_KEEP_DAYS, _iso, _parse,
)


class LogMixin:
    def active_of(self, kind: str):
        for cur in self.actives():
            if cur.get("type") == kind:
                return cur
        return None

    def note_close(self, now: datetime = None):
        """OIT 被手动关闭了 (X 的输入框第三段要写今天的这些时刻)."""
        now = (now or datetime.now()).replace(microsecond=0)
        closes = self._data.setdefault("closes", [])
        closes.append(_iso(now))
        del closes[:-50]
        self._dirty = True
        self.save()

    def _today_instruction_lines(self, now: datetime) -> list:
        day = now.strftime("%Y-%m-%d")
        rows = []
        for rec in self._data.get("history") or []:
            if isinstance(rec, dict) and str(rec.get("accepted") or "").startswith(day):
                word = "完成" if rec.get("result") == "completed" else "废弃"
                rows.append((str(rec.get("accepted")), str(rec.get("body") or ""), word))
        for cur in self.actives():
            if str(cur.get("accepted") or "").startswith(day):
                rows.append((str(cur.get("accepted")), str(cur.get("body") or ""), "进行中"))
        rows.sort(key=lambda r: r[0])
        out = []
        for accepted, body, word in rows:
            when = _parse(accepted)
            out.append(f"{when:%H:%M}  {tr(body)} ({tr(word)})" if when else f"{tr(body)} ({tr(word)})")
        return out

    def _today_close_lines(self, now: datetime) -> list:
        day = now.strftime("%Y-%m-%d")
        out = []
        for value in self._data.get("closes") or []:
            when = _parse(value)
            if when is not None and when.strftime("%Y-%m-%d") == day:
                out.append(f"{when:%H:%M}")
        return out

    def log_prefill(self, now: datetime = None) -> str:
        """X 的输入框一开始的文字: 四段 -- 今天的整点记录 (没启用是空白) / 今天专注的时间段 /
        今天领到的指令和状态 / 今天 OIT 被手动关闭的时刻. 第一次生成时存下来, 保存时拿它判断用户改没改."""
        cur = self.active_of("X")
        if cur is not None and isinstance(cur.get("log_base"), str):
            return cur["log_base"]
        now = now or datetime.now()
        try:
            hour = str(self.hour_log_fn() or "").strip()
        except Exception:       # noqa: BLE001
            hour = ""
        try:
            focus = str(self.focus_log_fn() or "").strip()
        except Exception:       # noqa: BLE001
            focus = ""
        text = "\n\n".join([hour, focus, "\n".join(self._today_instruction_lines(now)),
                              "\n".join(self._today_close_lines(now))])
        if cur is not None:
            cur["log_base"] = text
            self._dirty = True
        return text

    @staticmethod
    def _norm_log(text) -> str:
        return str(text or "").replace("\r\n", "\n").strip()

    def submit_log(self, text: str, now: datetime = None):
        """保存 X 的输入框: 一点没改视为作废, 改过视为完成 (文字存进 logs). 返回 "completed" / "void" / None."""
        cur = self.active_of("X")
        if cur is None:
            return None
        now = (now or datetime.now()).replace(microsecond=0)
        base = cur.get("log_base")
        if base is None:
            base = self.log_prefill(now)
        if self._norm_log(text) == self._norm_log(base):
            self._finish("void", now, cur=cur)
            return "void"
        logs = self._data.setdefault("logs", {})
        logs[now.strftime("%Y-%m-%d")] = str(text)
        for day in sorted(logs)[:-LOG_KEEP_DAYS]:
            logs.pop(day, None)
        self._finish("completed", now, float(cur.get("t") or FIXED_T["X"]), cur=cur)
        return "completed"

    def abandon_log(self, now: datetime = None):
        """输入框没保存就关了: 等于没改, 作废."""
        cur = self.active_of("X")
        if cur is not None:
            self._finish("void", (now or datetime.now()).replace(microsecond=0), cur=cur)
