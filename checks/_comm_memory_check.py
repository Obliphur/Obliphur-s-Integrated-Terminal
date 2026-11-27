# -*- coding: utf-8 -*-
"""验证第二步记忆系统升级：
1. persona_system 自动剥离纯机读模板块（保留 [tension]/[valence] 与日程正文，不影响 read_persona_full 规则解析）；
2. mem_live 短 ID 分配与多形式定位（E1/#E1/[#E1]/正文），兼容 callback_note；
3. 双端拆分 mem_auto_ts / mem_live_ts 独立合并（互不覆盖）及旧端单时间戳兼容；
4. 超出 KEEP=600 的本地冷归档 JSONL、L4 归档索引指针、oit_memory_search 检索与 clear 清理；
5. 每日睡眠期 Dreaming 触发、纠错、跨层晋升与说教未完成条目硬保护。
"""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import comm
from comm.comm_const import KEEP
from comm.comm_memory import (
    apply_dream_live_removals,
    apply_live_edit,
    archive_path,
    parse_dream_result,
    search_memory_archive,
    strip_machine_template_blocks,
)
from comm.comm_persona import persona_system, read_persona_full
from comm.comm_realism import callback_note
from mcp_bridge import OitMcpBridge


def main():
    tmp = tempfile.mkdtemp(prefix="oit_mem_check_")
    personas_dir = os.path.join(tmp, "personas")
    os.makedirs(personas_dir, exist_ok=True)
    try:
        # 1. templates 读取机制：persona_system 彻底排除 _templates 文件并剥离单文件机读块，read_persona_rules 带缓存读取规则
        from comm.comm_persona import read_persona_prompt, read_persona_rules
        sample_tpl = (
            "08:00-09:00 idle 晨间阅读\n"
            "[life:arcs]\narc1:[3,5] 阶段A | 阶段B\n[/life:arcs]\n"
            "[anti_repeat]\nwindow=10\nmin_hits=3\nforbid=秘密禁词\n[/anti_repeat]\n"
            "[callback]\nlookahead_days: 0\nexpire_days: 2\nmax_due: 2\n[/callback]\n"
            "[tension]\ncond_a:[20] 张力说明\n[/tension]\n"
            "[valence]\nval_a:[10] 效价说明\n[/valence]\n"
        )
        stripped = strip_machine_template_blocks(sample_tpl)
        assert "晨间阅读" in stripped
        assert "[life:arcs]" not in stripped and "阶段A" not in stripped
        assert "[anti_repeat]" not in stripped and "秘密禁词" not in stripped
        assert "[callback]" not in stripped
        assert "[tension]" not in stripped and "张力说明" not in stripped
        assert "[valence]" not in stripped and "效价说明" not in stripped

        with open(os.path.join(personas_dir, "TestP.txt"), "w", encoding="utf-8") as f:
            f.write("你是测试角色。\n")
        with open(os.path.join(personas_dir, "TestP_background.txt"), "w", encoding="utf-8") as f:
            f.write("这是角色背景。\n")
        with open(os.path.join(personas_dir, "TestP_templates.txt"), "w", encoding="utf-8") as f:
            f.write(sample_tpl)
        raw_full = read_persona_full(personas_dir, "TestP.txt")
        rules_txt = read_persona_rules(personas_dir, "TestP.txt")
        prompt_txt = read_persona_prompt(personas_dir, "TestP.txt")
        sys_prompt = persona_system(personas_dir, "TestP.txt")
        assert "[life:arcs]" in raw_full and "秘密禁词" in raw_full
        assert "[life:arcs]" in rules_txt and "张力说明" in rules_txt and "这是角色背景" not in rules_txt
        assert "你是测试角色" in prompt_txt and "这是角色背景" in prompt_txt
        assert "晨间阅读" not in prompt_txt and "[life:arcs]" not in prompt_txt and "张力说明" not in prompt_txt
        assert "晨间阅读" not in sys_prompt and "[life:arcs]" not in sys_prompt and "秘密禁词" not in sys_prompt and "张力说明" not in sys_prompt

        # 2. mem_live 短 ID 分配、ID/正文兼容定位与 callback_note 兼容
        ok, live, disp, err = apply_live_edit("", "add", "", "[回访:2026-11-27] 物理期末考")
        assert ok and live == "- [#E1] [回访:2026-11-27] 物理期末考", (ok, live, err)
        ok, live, disp, _ = apply_live_edit(live, "add", "", "说教主题：早睡，改变目标：23点前睡觉，未完成")
        assert ok and "[#E2]" in live
        # callback_note 必须能识别带 [#E1] 前缀的回访条目
        cb_note = callback_note("[callback]\nlookahead_days: 0\nexpire_days: 2\nmax_due: 2\n[/callback]", live, date(2026, 11, 27))
        assert "物理期末考" in cb_note and "今天到期" in cb_note, cb_note
        # 按短 ID 修改（支持 E1 / #E1 / [#E1]），保留原 ID
        ok, live, _, _ = apply_live_edit(live, "correct", "#E1", "[回访:2026-11-28] 物理期末考改期")
        assert ok and "- [#E1] [回访:2026-11-28] 物理期末考改期" in live
        # 按正文移除（兼容旧习惯）
        ok, live, _, _ = apply_live_edit(live, "remove", "[回访:2026-11-28] 物理期末考改期", "")
        assert ok and "物理期末考" not in live and "[#E2]" in live

        # 3. 双端拆分 mem_auto_ts / mem_live_ts 独立合并（互不覆盖）
        now_t = [datetime(2026, 11, 27, 2, 0, 0)]
        c1 = comm.Comm(tmp, personas_dir, now=lambda: now_t[0])
        c1.set_persona("TestP.txt")
        base_ms = c1._ms()
        c1.cfg["mem_auto"] = "- 旧长期事实"
        c1.cfg["mem_auto_ts"] = base_ms + 100
        c1.cfg["mem_live"] = "- [#E1] 本地新实时事件"
        c1.cfg["mem_live_ts"] = base_ms + 500
        c1.cfg["mem_ts"] = base_ms + 500
        c1.save_chat()

        # 模拟另一台设备：mem_auto 更新（at=600 > 100），但 mem_live 较旧（et=200 < 500）
        c1.apply_doc({
            f"mem:{c1.pid}": {
                "a": "- 远端更新的长期事实",
                "e": "- [#E1] 远端旧实时事件",
                "u": base_ms + 50,
                "t": base_ms + 600,
                "at": base_ms + 600,
                "et": base_ms + 200,
            }
        })
        assert c1.cfg["mem_auto"] == "- 远端更新的长期事实", c1.cfg["mem_auto"]
        assert c1.cfg["mem_live"] == "- [#E1] 本地新实时事件", c1.cfg["mem_live"]
        doc_out = c1.sync_doc()
        assert doc_out[f"mem:{c1.pid}"]["at"] == base_ms + 600
        assert doc_out[f"mem:{c1.pid}"]["et"] == base_ms + 500

        # 兼容旧端无 at/et 的整块同步包
        c1.apply_doc({
            f"mem:{c1.pid}": {
                "a": "- 旧端整块长期",
                "e": "- 旧端整块实时",
                "u": base_ms + 700,
                "t": base_ms + 900,
            }
        })
        assert c1.cfg["mem_auto"] == "- 旧端整块长期" and c1.cfg["mem_live"] == "- 旧端整块实时"

        # 4. 超出 KEEP=600 的本地冷归档 JSONL、L4 目录指针、oit_memory_search 与 clear 清理
        c1.messages = []
        start_dt = datetime(2026, 10, 1, 10, 0, 0)
        for i in range(KEEP + 35):
            dt = start_dt + timedelta(minutes=i)
            text = "我们约定冬天一起去北海道看雪" if i == 5 else f"普通对话消息编号 {i}"
            c1.messages.append({
                "id": i + 1,
                "uid": f"u_{i}",
                "role": "me" if i % 2 == 0 else "ai",
                "text": text,
                "at": dt.isoformat(timespec="seconds"),
                "ts": int(dt.timestamp() * 1000),
            })
        c1.save_chat()
        c1.flush()
        assert len(c1.messages) <= KEEP
        ap = archive_path(tmp, c1.pid)
        assert os.path.isfile(ap), "超出 KEEP 的旧消息必须归档到 JSONL"
        mb = c1.memory_block()
        assert "【可检索档案】" in mb and "oit_memory_search" in mb, mb
        sr = search_memory_archive(tmp, c1.pid, c1.messages, query="北海道 看雪", limit=5)
        assert sr["ok"] and sr["matched"] >= 1 and sr["results"][0]["source"] == "archive"
        assert "北海道" in sr["results"][0]["text"] and "before" in sr["results"][0] and "after" in sr["results"][0]

        # 5. 睡眠期 Dreaming 解析、硬保护说教未完成条目、每日去重
        c1.cfg["mem_auto"] = "- 喜欢猫?\n- 住在上海"
        c1.cfg["mem_live"] = (
            "- [#E1] [回访:2026-11-20] 上周面试结果\n"
            "- [#E2] 说教主题：拖延，改变目标：每天先做数学，未完成\n"
            "- [#E3] 喜欢喝燕麦拿铁"
        )
        c1.cfg["dream_day"] = ""
        parsed = parse_dream_result(
            '```json\n{"mem_auto": "- 确认养了一只橘猫\\n- 住在上海\\n- 喜欢喝燕麦拿铁", "remove_live_ids": ["E1", "E2", "E3"]}\n```'
        )
        assert parsed and parsed["remove_live_ids"] == ["E1", "E2", "E3"]
        new_live, removed = apply_dream_live_removals(c1.cfg["mem_live"], parsed["remove_live_ids"])
        assert removed == ["E1", "E3"], f"说教未完成条目 E2 必须受硬保护不得移除: {removed}"
        assert "[#E2]" in new_live and "[#E1]" not in new_live and "[#E3]" not in new_live

        # 通过 _dream_finish 走完整落盘与 dream_day 标记
        c1._dream_finish(
            json.dumps({"mem_auto": "- 确认养了一只橘猫\n- 喜欢喝燕麦拿铁", "remove_live_ids": ["E1", "E2", "E3"]}, ensure_ascii=False),
            "", c1._ms(), int(c1.cfg.get("clear_ts") or 0), "2026-11-27", c1.pid,
        )
        assert c1.cfg["dream_day"] == "2026-11-27"
        assert "确认养了一只橘猫" in c1.cfg["mem_auto"]
        assert "[#E2]" in c1.cfg["mem_live"] and "[#E1]" not in c1.cfg["mem_live"]
        assert c1._dream_due() is False, "同一天已完成 Dreaming 后不得重复触发"

        # 6. MCP Bridge 侧 oit_memory_search 与 Dreaming save_auto 验证
        bridge = OitMcpBridge(c1, tmp)
        res_search = bridge.handle_tool_execute({"name": "oit_memory_search", "arguments": {"query": "北海道"}})
        assert res_search["ok"] and res_search["matched"] >= 1
        c1.clear()
        assert not os.path.isfile(ap), "清空对话时必须同步清理冷归档 JSONL"

        # 7. Gemina / Charlotte 内置文件自动升级：未改过的旧内置版本自动补齐 [burst:consent]，用户自定义修改不覆盖
        up_tmp = tempfile.mkdtemp(prefix="oit_builtin_up_")
        up_p = os.path.join(up_tmp, "personas")
        os.makedirs(up_p, exist_ok=True)
        try:
            c_up = comm.Comm(up_tmp, up_p, now=lambda: now_t[0])
            c_up.flush()
            # 模拟老用户：builtin_bases 为旧版 CRC 且模板缺失 [burst:consent]
            old_tpl = "【日程】\n08:00-09:00 idle\n"
            with open(os.path.join(up_p, "Gemina_templates.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write(old_tpl)
            with open(os.path.join(up_p, "Charlotte_templates.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("用户自定义的 Charlotte 模板\n")
            c_up.cfg["builtin_bases"] = {
                "Gemina_templates.txt": comm.Comm._text_crc(old_tpl),
                "Charlotte_templates.txt": "00000000",
            }
            c_up.save_cfg()
            c_up.flush()
            c_up2 = comm.Comm(up_tmp, up_p, now=lambda: now_t[0])
            c_up2.set_persona("Gemina.txt")
            assert "private_contact" in c_up2.burst_consent_rules_for(), "未修改的旧版 Gemina_templates 应自动升级补齐 [burst:consent]"
            with open(os.path.join(up_p, "Charlotte_templates.txt"), encoding="utf-8") as f:
                assert f.read().strip() == "用户自定义的 Charlotte 模板", "用户手动改过的内置配套文件绝不能被覆盖"
            # CRLF 归一化与 Claudia claudia_differs() 假阳性防护
            c_up2.set_persona("Claudia.txt")
            assert not c_up2.claudia_differs(), "默认 Claudia 不应误报与默认人设不同"
            assert comm.Comm._text_crc("line1\r\nline2") == comm.Comm._text_crc("line1\nline2")
            c_up2.flush()
        finally:
            shutil.rmtree(up_tmp, ignore_errors=True)

        print("COMM MEMORY CHECK OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
