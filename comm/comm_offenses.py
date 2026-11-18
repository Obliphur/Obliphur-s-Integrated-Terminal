# -*- coding: utf-8 -*-
"""各人设独立的冒犯记录；原谅保留同步墓碑，摘要和时间流逝不能删除。"""

import json
import re
from datetime import datetime

import config
from i18n import tr


def offenses_norm(value):
    records = {}
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        uid = item.get("id")
        reason = item.get("reason")
        if not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", uid):
            continue
        if not isinstance(reason, str):
            continue
        reason = " ".join(reason.split())[:120]
        forgiven = item.get("forgiven") is True
        if not reason and not forgiven:
            continue
        try:
            at, ts = int(item.get("at") or 0), int(item.get("ts") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if not 0 < at <= 2**63 - 1 or not 0 < ts <= 2**63 - 1:
            continue
        record = {"id": uid, "at": at, "reason": "" if forgiven else reason, "ts": ts, "forgiven": forgiven}
        old = records.get(uid)
        if old is None or (forgiven, ts, record["reason"]) > (old["forgiven"], old["ts"], old["reason"]):
            records[uid] = record
    return sorted(records.values(), key=lambda r: (r["at"], r["id"]))


def offenses_active(d):
    return [r for r in offenses_norm((d or {}).get("offenses")) if not r["forgiven"]]


def offenses_rate(d):
    return max(0.0, round(1.0 - len(offenses_active(d)) * config.COMM_OFFENSE_VALENCE_RATE_PENALTY, 10))


def offenses_apply(d, reply, index, uid, sent, now_ms):
    """只处理本轮待判断的真实用户消息，同一消息只记一条，不提供直接删除操作。"""
    from ai_assist.ai_tools import args_of, unpack
    if unpack(reply) is None:
        return
    records = {r["id"]: r for r in offenses_norm(d.get("offenses"))}
    existing = set(records)
    for event in args_of(reply).get("offenses", []):
        if event.get("index") != index:
            continue
        action, target, reason = event.get("action"), event.get("target"), event.get("reason", "")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 120 or "\n" in reason or "\r" in reason or "【系统记录】" in reason:
            continue
        if action == "add" and target == "" and uid not in records and sent > 0:
            records[uid] = {"id": uid, "at": sent, "reason": reason.strip(), "ts": now_ms, "forgiven": False}
        elif action == "forgive" and target in existing and not records[target]["forgiven"]:
            old = records[target]
            records[target] = dict(old, reason="", forgiven=True, ts=max(now_ms, old["ts"] + 1))
    d["offenses"] = offenses_norm(list(records.values()))


def offenses_line(d):
    records = offenses_active(d)
    if not records:
        return ""
    def time_text(at):
        try:
            return datetime.fromtimestamp(at / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OverflowError, OSError):
            return str(at)
    values = [{"id": r["id"], "time": time_text(r["at"]),
               "reason": r["reason"]} for r in records]
    return tr("- 【系统记录】未获原谅的冒犯 ({count} 条): {records}", count=len(records),
              records=json.dumps(values, ensure_ascii=False, separators=(",", ":")))
