# -*- coding: utf-8 -*-
"""沟通工具调度；查询即时返回，写操作在回复提交时由主线程复核。"""

import json
import threading
import config
import music_library
from i18n import tr

from ai_assist.ai_common import AIError
from ai_assist.ai_tools import unpack
from comm.comm_prompt import native_persona_text
from comm.comm_persona import MEM_MAX
from comm.comm_util import is_claudia


class ToolsMixin:
    def _knowledge_call(self, task, *, image=None, timeout=90):
        from ai_assist.ai_subagent import invoke
        profile = self.ai.task_profile("subject", fallback=self.ai.config_for(self.persona_name()))
        return invoke(profile, "subject", task, image=image, max_tokens=1600, temperature=.1,
                      timeout=timeout, chat=self.chat_fn)

    def _knowledge_subject(self, domain, session, question, image=None):
        from knowledge import get_knowledge
        from knowledge.kb_ai import prepare_subject, finish_subject
        kb = get_knowledge(self.dir)
        epoch = self._epoch
        task, hits = prepare_subject(kb, self._knowledge_call, domain, question, image)
        answer = self.subagent_call("subject", session, task, image=image,
                                   max_tokens=1400, temperature=.15, timeout=180)
        clean, citations = finish_subject(kb, self._knowledge_call, domain, question, answer, hits,
                                          valid=lambda: self._epoch == epoch)
        return clean, citations

    def _music_access(self):
        return self.related(), self._interaction_prepare()["valence"]

    def _tool_rounds(self, ask, convo, reply, kind, epoch):
        from comm.comm_const import OP_KINDS
        op, slots, last = None, None, []
        music_ids = set()
        for round_index in range(config.COMM_TOOL_ROUNDS + 1):
            data = unpack(reply)
            if data is None or data["name"] in ("", "oit_reply"):
                if data and data["name"] == "oit_reply" and data["arguments"].get("kb_citations"):
                    from knowledge import get_knowledge
                    self._kb_citations_pending = get_knowledge(self.dir).resolve_citations(data["arguments"]["kb_citations"])
                return op, reply
            if round_index >= config.COMM_TOOL_ROUNDS:
                raise AIError("format", "工具调用轮次超限")
            if self._epoch != epoch or self.hard_block():
                raise AIError("format", "工具请求已失效")
            name, args = data["name"], data["arguments"]
            result = {"ok": False, "error": "当前不允许此工具"}
            if name in ("oit_kb_search", "oit_kb_trace", "oit_kb_record_use", "oit_kb_propose"):
                from knowledge import get_knowledge
                from knowledge.kb_ai import bridge_tool
                try:
                    result = bridge_tool(get_knowledge(self.dir), name, args)
                except (ValueError, TypeError, OSError) as error:
                    result = {"ok": False, "error": str(error)[:500]}
            elif name == "oit_memory_edit" and op is None:
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
            elif name == "oit_subject":
                domain = args["domain"]
                session = args["session"]
                question = args["question"].strip()
                try:
                    answer, citations = self._knowledge_subject(domain, session, question, getattr(self, "_active_image", None))
                    self._kb_citations_pending = citations
                    img_on = self.answer_images_on()
                    if img_on:
                        self._answer_image_pending = {
                            "answer": answer, "image": getattr(self, "_active_image", None),
                            "domain": domain, "session": session, "epoch": epoch,
                        }
                    instruction = (
                        "把子代理答案作为事实依据，按人设风格化改写；不要声称用户看到了子代理。"
                        + ("当前解答图片模式已开启：请在 oit_reply 前调用 oit_answer_image(domain, session, answer, styled_answer)，再用简洁自然且不含 Markdown/LaTeX 源码的文字调用 oit_reply。"
                           if img_on else "当前解答图片模式已关闭：请直接用 oit_reply 提交风格化文字回复。")
                    )
                    result = {"ok": True, "domain": domain, "session": session,
                              "answer": answer, "answer_image_mode": img_on,
                              "kb_citations": citations,
                              "instruction": instruction}
                except Exception as e:  # noqa: BLE001
                    self.log(f"TOOL subject FAIL {type(e).__name__}")
                    result = {"ok": False, "error": str(e)[:500]}

            elif name == "oit_answer_image":
                if not self.answer_images_on():
                    result = {"ok": True, "enabled": False,
                              "instruction": "解答图片模式已关闭；请仅调用 oit_reply 提交文字回复。"}
                else:
                    domain = str(args.get("domain") or "math")
                    session = str(args.get("session") or "default")
                    original = str(args.get("answer") or "").strip()[:8000]
                    styled = str(args.get("styled_answer") or "").strip()[:8000]
                    if not original or not styled:
                        result = {"ok": False, "error": "answer 和 styled_answer 均不能为空"}
                    else:
                        prev = self._answer_image_pending if isinstance(self._answer_image_pending, dict) else {}
                        self._answer_image_pending = {
                            "answer": original,
                            "styled_answer": styled,
                            "image": prev.get("image") if prev else getattr(self, "_active_image", None),
                            "domain": domain,
                            "session": session,
                            "epoch": epoch,
                        }
                        result = {"ok": True, "ready": True,
                                  "instruction": "解答图片已登记；请随即调用 oit_reply，图片将作为独立气泡发送（可在 text 中用 <oit_message> 分条并用独占一行的 <oit_answer_image> 指定发图位置，发图前后均可各加一句话作为独立气泡；气泡正文不要输出 Markdown/LaTeX 源码）。"}

            elif name == "oit_reading_memory":
                fn = getattr(self, "coread_memory_fn", None)
                if not fn:
                    result = {"ok": False, "error": "共读书库服务不可用"}
                else:
                    try:
                        action = str(args.get("action") or "lookup")
                        if action == "save":
                            if not getattr(self, "reading_mode", False):
                                # 允许在普通聊天中修订已有书籍记忆，但只能由已存在的书籍编号定位。
                                if not args.get("book_id"):
                                    raise ValueError("非共读状态保存记忆必须提供book_id")
                            if not args.get("book_id") or not args.get("chapter"):
                                raise ValueError("保存章节记忆需要book_id和chapter")
                        result = fn(action=action, book_id=args.get("book_id"), book_title=args.get("book_title", ""),
                                    chapter=args.get("chapter", ""), chapter_title=args.get("chapter_title", ""),
                                    summary=args.get("summary", ""), annotations=args.get("annotations", ""),
                                    mode=args.get("mode", "replace"))
                        result = {"ok": True, **(result if isinstance(result, dict) else {"data": result}),
                                  "instruction": ("共读记忆已更新；继续围绕当前章节自然回复。" if action == "save"
                                                   else "若书名与当前讨论相关，请只使用返回的章节记忆和批注作为依据。")}
                    except Exception as e:
                        result = {"ok": False, "error": str(e)[:500]}
            elif name == "oit_reading_acquire" and getattr(self, "reading_mode", False):
                try:
                    result = self.coread_acquire_fn(args["source_url"],
                        lambda: self._epoch == epoch and getattr(self, "reading_mode", False) and not self.hard_block())
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:500]}
            elif name == "oit_reading_books" and getattr(self, "reading_mode", False):
                try:
                    result = {"ok": True, "books": self.coread_books_fn()}
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:500]}
            elif name == "oit_reading_page" and getattr(self, "reading_mode", False):
                try:
                    result = {"ok": True, "page": self.coread_read_fn(args["book_id"], args["page"])}
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:500]}
            elif name == "oit_reading_comment" and getattr(self, "reading_mode", False):
                try:
                    result = self.coread_comment_fn(args["book_id"], args["paragraph_idx"],
                                                    args["content"], args.get("selected_text", ""))
                except Exception as e:
                    result = {"ok": False, "error": str(e)[:500]}
            elif name == "oit_reading_end":
                if not getattr(self, "reading_mode", False):
                    result = {"ok": False, "error": "当前没有正在进行的共同阅读"}
                elif kind not in OP_KINDS or op is not None:
                    result = {"ok": False, "error": "当前请求不能结束共同阅读"}
                else:
                    op = {"type": "coread_end", "session": self.coread_session_id}
                    result = {"ok": True, "status": "queued_until_message_visible",
                              "next": "先用oit_reply提交自然的结束说明；正文实际显示后共同阅读才结束"}
            elif name == "oit_reading_offer":
                if kind not in OP_KINDS:
                    result = {"ok": False, "error": "此类请求不能发起共同阅读"}
                elif not is_claudia(self.persona_name()):
                    result = {"ok": False, "error": "只有 Claudia 可以发起共同阅读申请"}
                elif getattr(self, "reading_mode", False) or self.coread_offer():
                    result = {"ok": False, "error": "已有共同阅读或待处理的申请"}
                elif (kind == "proactive" and not self._coread_quote_is_recent(args.get("free_statement", ""))):
                    result = {"ok": False, "error": "主动申请需要引用用户近30分钟内的原话"}
                elif (kind != "proactive" and not any(self._coread_quote_is_recent(args.get(field, ""))
                                                       for field in ("free_statement", "request_statement"))):
                    result = {"ok": False, "error": "申请需要引用用户近30分钟内的原话"}
                else:
                    op = {"type": "coread_offer", "free_statement": args.get("free_statement", ""),
                          "request_statement": args.get("request_statement", "")}
                    result = {"ok": True, "status": "queued_until_message_visible",
                              "next": "在oit_reply中自然邀请用户共同阅读；用户同意后才能打开阅读室"}
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
                elif is_claudia(persona) and self.offense_blocked():
                    result = {"ok": False, "error": "Claudia 尚未原谅用户的冒犯，不会演奏"}
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
            if is_claudia(self.persona_name()) and (valence < 30 or self.offense_blocked()):
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
        if not self.music_player.allowed() or self.offense_blocked():
            self.music_player.stop()
        if self.offense_blocked():
            self._music_actions.clear()
        if self.music_player.error:
            self.error, self.music_player.error = self.music_player.error, ""
            self._changed()
        if self._music_actions:
            for message in self.messages:
                if message["id"] in self._music_actions:
                    self._music_deliver(message)

    def _answer_image_after_reply(self, message, pending):
        """聊天 AI 的风格化回复完成后，再让学科子代理整理成排版稿并截图。"""
        if not pending or not self.answer_images_on():
            return
        epoch = int(pending.get("epoch") or 0)
        original = str(pending.get("answer") or "")[:8000]
        styled = str(pending.get("styled_answer") or message.get("text") or "")[:8000]
        if not original or not styled:
            return
        message["_img_wait_until"] = self._ms() + 30000

        def work():
            attached = False
            try:
                prompt = (
                    "请根据下面的原始学科解答和聊天 AI 已经写好的用户可见版本，生成一份最终的解答图片排版稿。"
                    "保留原始解答中可核验的关键逻辑、步骤与结论；以聊天 AI 的语言风格为准。"
                    "使用 Markdown 标题、列表和 LaTeX 公式，内容完整但紧凑；不要输出解释、代码围栏或隐藏思维过程。\n\n"
                    f"原始学科解答:\n{original}\n\n聊天 AI 风格化版本:\n{styled}"
                )
                try:
                    formatted = self.subagent_call(
                        "subject", f"answer-image:{pending.get('session') or self.persona_name()}", prompt,
                        max_tokens=1400, temperature=0.1, timeout=180)
                except Exception as sub_err:  # noqa: BLE001
                    self.log(f"ANSWER_IMAGE format fallback ({type(sub_err).__name__}): {sub_err}")
                    formatted = (
                        f"{styled}\n\n## 详细推导\n\n{original}"
                        if len(styled) < 40 and len(original) > len(styled) * 2
                        else styled
                    )
                if self._epoch != epoch or not self.answer_images_on():
                    return
                import answer_images
                citations = message.get("kb_citations", [])
                if citations:
                    formatted += "\n\n" + tr("引用技巧") + ": " + "; ".join(c["title"] + " ← " + " ← ".join(p["title"] for p in c.get("lineage", [])) for c in citations)
                data = answer_images.render(formatted, pending.get("image"), title=f"{pending.get('domain') or '学科'}解答")
                if not data:
                    detail = answer_images.last_error() if hasattr(answer_images, "last_error") else ""
                    self.log(f"ANSWER_IMAGE renderer unavailable: {detail}" if detail else "ANSWER_IMAGE renderer unavailable")
                    return
                name = self._save_chat_image(message["uid"] + "-answer", data)
                if name and self._epoch == epoch:
                    message["img"] = name
                    message["answer_image"] = True
                    attached = True
                    self.save_chat()
                    self._push()
            except Exception as e:  # noqa: BLE001
                self.log(f"ANSWER_IMAGE FAIL {type(e).__name__}: {e}")
            finally:
                message.pop("_img_wait_until", None)
                if not attached and message.get("hold") and message.get("text") == "[图片]" and not message.get("img"):
                    try:
                        self.messages.remove(message)
                    except ValueError:
                        pass
                self._changed()

        threading.Thread(target=work, daemon=True).start()
