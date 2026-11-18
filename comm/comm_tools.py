# -*- coding: utf-8 -*-
"""沟通工具调度；查询即时返回，写操作在回复提交时由主线程复核。"""

import json
import config
import music_library
from i18n import tr

from ai_assist.ai_common import AIError
from ai_assist.ai_tools import unpack
from comm.comm_prompt import native_persona_text
from comm.comm_persona import MEM_MAX
from comm.comm_util import is_claudia


class ToolsMixin:
    def _music_access(self):
        return self.related(), self._interaction_prepare()["valence"]

    def _tool_rounds(self, ask, convo, reply, kind, epoch):
        from comm.comm_const import OP_KINDS
        op, slots, last = None, None, []
        music_ids = set()
        for round_index in range(config.COMM_TOOL_ROUNDS + 1):
            data = unpack(reply)
            if data is None or data["name"] in ("", "oit_reply"):
                return op, reply
            if round_index >= config.COMM_TOOL_ROUNDS:
                raise AIError("format", "工具调用轮次超限")
            if self._epoch != epoch or self.hard_block():
                raise AIError("format", "工具请求已失效")
            name, args = data["name"], data["arguments"]
            result = {"ok": False, "error": "当前不允许此工具"}
            if name == "oit_memory_edit" and op is None:
                action, target, content = args["action"], args["target"].strip(), args["text"].strip()
                entries = [line[2:].strip() for line in str(self.cfg.get("mem_live") or "").splitlines()
                           if line.strip().startswith("- ")]
                valid_text = "\n" not in content and "\r" not in content and "【系统记录】" not in content
                if action == "add" and not target and content and valid_text and content not in entries and len(entries) < 15:
                    op = {"type": "memory_event", "action": action, "target": "", "text": content}
                elif action in ("correct", "remove") and target in entries and (action == "remove" or content and valid_text):
                    op = {"type": "memory_event", "action": action, "target": target, "text": content}
                else:
                    result = {"ok": False, "error": "记忆事件不匹配或已达到数量上限"}
                if op:
                    result = {"ok": True, "status": "queued_until_message_visible",
                              "next": "正式回复显示时保存这条实时记忆事件"}
            elif name == "oit_query":
                values = {}
                for module in args["modules"]:
                    try:
                        if module == "condition":
                            values[module] = {"type": "condition", "data": native_persona_text(self.condition_text())}
                        elif module == "messages":
                            values[module] = {"type": "count", "data": native_persona_text(self.messages_text())}
                        else:
                            own = self.routine_info(module)
                            text = own or str(self.info_fn(module) or "")
                            values[module] = {"type": "own_data" if own else "reference_template", "data": native_persona_text(text), "available": bool(text)}
                    except Exception as e:
                        self.log(f"TOOL query {module} FAIL {type(e).__name__}")
                        values[module] = {"available": False, "error": "unavailable"}
                result = {"ok": True, "data": values, "note": "own_data属于你，不是用户；reference_template仅参考格式与占位符，非真实数据。不可用数据不要编造。"}
            elif name == "oit_music_catalog":
                try:
                    library = music_library.manager(self.dir)
                    persona = self.persona_name()
                    related, valence = self._music_access()
                    entries = library.catalog(persona, related, valence)[:80]
                    music_ids = {item["id"] for item in entries}
                    result = {"ok": True, "data": entries, "note": "乐谱是本机文件；标签仅供选曲参考。用户上传曲目不会随聊天记录同步。"}
                except Exception as e:
                    self.log(f"TOOL music catalog FAIL {type(e).__name__}")
                    result = {"ok": False, "error": "本地曲库暂时不可用"}
            elif name == "oit_music_select" and music_ids and op is None:
                score_id = args["id"]
                try:
                    if score_id not in music_ids:
                        raise music_library.MusicLibraryError("乐谱不在刚查询的目录中")
                    library = music_library.manager(self.dir)
                    persona = self.persona_name()
                    related, valence = self._music_access()
                    item, path = library.get(score_id, persona, related, valence)
                    if not item or not path or not path.is_file():
                        raise music_library.MusicLibraryError("乐谱已不存在")
                    op = {"type": "music_select", "id": score_id}
                    result = {"ok": True, "selected": {"id": score_id, "title": item["title"],
                              "composer": item.get("composer", "")}, "status": "queued_until_message_visible",
                              "next": "在oit_reply中自然回复；正式回复显示时该曲目才成为当前选曲，尚未播放"}
                except music_library.MusicLibraryError as e:
                    result = {"ok": False, "error": str(e)}
            elif name == "oit_music_generate_midi":
                score_id = args["id"]
                library = music_library.manager(self.dir)
                persona = self.persona_name()
                related, valence = self._music_access()
                selected = library.selection(persona, related, valence)
                pending_id = op.get("id") if isinstance(op, dict) and op.get("type") == "music_select" else ""
                if score_id != (selected or {}).get("id") and score_id != pending_id:
                    result = {"ok": False, "error": "请先通过oit_music_select选择这首乐谱"}
                else:
                    try:
                        generated = library.generate_midi(score_id, persona, related, valence)
                        result = {"ok": True, "data": {"id": generated["id"], "title": generated["title"],
                                  "format": "MIDI", "humanized": True, "reused": generated["reused"]},
                                  "next": "MIDI 文件已生成，可以告知用户；这不表示已经播放或生成 MP3"}
                    except music_library.MusicLibraryError as e:
                        result = {"ok": False, "error": str(e)}
            elif name == "oit_music_perform" and (op is None or op.get("type") == "music_select"):
                score_id = args["id"]
                library = music_library.manager(self.dir)
                persona = self.persona_name()
                related, valence = self._music_access()
                selected = library.selection(persona, related, valence)
                pending_id = op.get("id") if isinstance(op, dict) and op.get("type") == "music_select" else ""
                if score_id != (selected or {}).get("id") and score_id != pending_id:
                    result = {"ok": False, "error": "请先通过oit_music_select选择这首乐谱"}
                elif kind not in ("user", "retry", "followup"):
                    result = {"ok": False, "error": "只能响应用户明确的演奏请求"}
                elif is_claudia(persona) and valence < 30:
                    result = {"ok": False, "error": "Claudia 当前效价低于演奏意愿门槛"}
                elif self.state_fn() not in ("idle", "break"):
                    result = {"ok": False, "error": "仅可在空闲或休息期间演奏"}
                else:
                    try:
                        item, path = library.get(score_id, persona, related, valence)
                        if not item or not path or not path.is_file():
                            raise music_library.MusicLibraryError("乐谱已不存在")
                        performance = library.prepare_performance(score_id, persona, related, valence)
                        op = {"type": "music_perform", "id": score_id, "path": performance["path"]}
                        result = {"ok": True, "selected": {"id": score_id, "title": item["title"]},
                                  "status": "queued_until_message_visible", "format": "MP3",
                                  "next": "音频已准备好；在oit_reply中自然说明曲目；正式回复显示且用户空闲或休息时才开始播放，尚未播放"}
                    except music_library.MusicLibraryError as e:
                        result = {"ok": False, "error": str(e)}
            elif name == "oit_music_stop" and kind in ("user", "retry", "followup") and op is None:
                op = {"type": "music_stop"}
                result = {"ok": True, "status": "queued_until_reply", "next": "提交oit_reply后停止本机演奏并取消待播曲目"}
            elif name == "oit_joint_offer":
                why = "此类请求不能发起联合行动" if kind not in OP_KINDS else self.joint_block() or ("正在联合行动同步" if self.joint_live() else "")
                slots = None if why else self._op_slots(self.now())
                if slots:
                    last = list(self.cfg.get("joint_last") or [])
                    result = {"ok": True, "data": self._op_offer(slots, last, native=True), "next": "oit_joint_select(terms:编号数组)，尚未发出邀请"}
                else:
                    result = {"ok": False, "error": why or "未来24小时没有合适的开始时间"}
            elif name == "oit_joint_select" and slots and op is None and kind in OP_KINDS:
                terms = self._op_terms(args["terms"], last)
                op = self._op_plan(terms, slots)
                result = {"ok": bool(op), "plan": op, "status": "queued_until_message_visible" if op else "no_available_slot",
                          "next": "调用oit_reply向用户发出邀请，邀请随正文实际显示生效" if op else "没有发出邀请，请正常回复用户"}
            convo += [{"role": "assistant", "content": reply}, {"role": "user", "content": json.dumps(result, ensure_ascii=False)}]
            reply = ask(convo)
        raise AIError("format", "工具调用轮次超限")

    def _memory_event_apply(self, op):
        entries = [line[2:].strip() for line in str(self.cfg.get("mem_live") or "").splitlines()
                   if line.strip().startswith("- ")]
        action, target, content = op.get("action"), op.get("target", ""), op.get("text", "")
        valid_text = isinstance(content, str) and "\n" not in content and "\r" not in content and "【系统记录】" not in content
        if action == "add" and target == "" and content and valid_text and content not in entries and len(entries) < 15:
            entries.append(content)
        elif action == "correct" and target in entries and content and valid_text:
            entries[entries.index(target)] = content
        elif action == "remove" and target in entries:
            entries.remove(target)
        else:
            return False
        text = "\n".join("- " + item for item in entries)
        if len(text) > MEM_MAX:
            return False
        self.cfg["mem_live"] = text
        self.cfg["mem_ts"] = self._ms()
        return True

    def _music_deliver(self, message):
        """本机消息实际显示时消费演奏动作；动作不进入聊天存档和同步。"""
        if message.get("hold"):
            return
        pending = self._music_actions.pop(message["id"], None)
        if not pending:
            return
        epoch, op = pending
        if epoch != self._epoch or not self.music_player.allowed():
            return
        try:
            related, valence = self._music_access()
            if is_claudia(self.persona_name()) and valence < 30:
                return
            library = music_library.manager(self.dir)
            item, path = library.get(op["id"], self.persona_name(), related, valence)
            if not item or not path or not path.is_file():
                return
            library.select(op["id"], self.persona_name(), related, valence)
            if not self.music_player.play(op["path"]):
                self.error = tr("演奏播放失败.")
        except (OSError, ValueError) as error:
            self.error = str(error)
            self.log(f"MUSIC perform FAIL {type(error).__name__}")

    def _music_tick(self):
        if not self.music_player.allowed():
            self.music_player.stop()
        if self.music_player.error:
            self.error, self.music_player.error = self.music_player.error, ""
            self._changed()
        if self._music_actions:
            for message in self.messages:
                if message["id"] in self._music_actions:
                    self._music_deliver(message)
