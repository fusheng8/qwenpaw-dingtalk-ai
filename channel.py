"""One inbound message -> one durable AI card, including approvals."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

import dingtalk_stream
from qwenpaw.app.channels.dingtalk.channel import DingTalkChannel
from qwenpaw.config import get_config_path

from .state import Store, Turn, TERMINAL, PRIVATE_FIELDS, APPROVAL_FIELDS, identity, project, detail, text, tool_result_status
from .transport import CardTransport

logger = logging.getLogger(__name__)


def value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


class CardCallback(dingtalk_stream.CallbackHandler):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def process(self, message):
        # DingTalk requires a response in two seconds. No network calls here.
        payload = message.data
        if isinstance(payload, str):
            payload = json.loads(payload)
        future = asyncio.run_coroutine_threadsafe(self.channel.card_callback(payload), self.channel._loop)
        try:
            response = await asyncio.wait_for(asyncio.wrap_future(future), timeout=1.7)
        except Exception:
            response = self.channel.callback_response(success=False, private={"detailVisible": "yes",
                "detailTitle": "操作未完成", "detailBody": "请稍后重试；审批状态以最新卡片为准。"})
        return dingtalk_stream.AckMessage.STATUS_OK, response


class DingTalkAIChannel(DingTalkChannel):
    channel = "dingtalk_ai"

    def __init__(self, *args, retention_days=30, page_bytes=1800, top_template_id="", **kwargs):
        kwargs.update(message_type="card", streaming_enabled=True, no_text_debounce=True,
                      share_session_in_group=False, card_template_key="content")
        super().__init__(*args, **kwargs)
        self.top_template_id = str(top_template_id).strip()
        self.top_lock = asyncio.Lock()
        self.top_event = asyncio.Event()
        self.top_task = None
        self.top_views = {}
        self.stopping = False
        self.retention_days = max(1, min(int(retention_days), 365))
        self.page_bytes = max(512, min(int(page_bytes), 2400))
        directory = (self._workspace_dir or get_config_path().parent) / "dingtalk-ai"
        self.store = Store(directory / "turns.sqlite3")
        self.transport = CardTransport(self.client_id, self.client_secret, self.card_template_id, self.robot_code)
        self.turns = {}
        self.flush_tasks = {}
        self.locks = {}
        self.watchers = set()
        self.last_flush = {}
        self.last_stream = {}
        self.tool_message_ids = {}

    @classmethod
    def from_config(cls, process, config, on_reply_sent=None, display_config=None,
                    no_text_debounce=True, workspace_dir=None):
        get = lambda key, default=None: value(config, key, default)
        return cls(process=process, enabled=get("enabled", False),
            client_id=get("client_id", ""), client_secret=get("client_secret", ""),
            bot_prefix=get("bot_prefix", ""), card_template_id=get("card_template_id", ""),
            robot_code=get("robot_code", ""), top_template_id=get("top_template_id", ""), workspace_dir=workspace_dir,
            on_reply_sent=on_reply_sent, display_config=display_config,
            require_mention=get("require_mention", True),
            dm_policy=get("dm_policy", "open"), group_policy=get("group_policy", "open"),
            allow_from=get("allow_from", []), deny_message=get("deny_message", ""),
            retention_days=get("retention_days", 30), page_bytes=get("page_bytes", 1800))

    def resolve_session_id(self, sender_id, channel_meta=None):
        cid = (channel_meta or {}).get("conversation_id", "")
        key = f"{self.client_id}:{cid}:{sender_id}"
        return self.channel + ":" + hashlib.sha256(key.encode()).hexdigest()[:32]

    def build_agent_request_from_native(self, native_payload):
        return super().build_agent_request_from_native({**native_payload, "channel_id": self.channel})

    def merge_native_items(self, items):
        # The manager drains a session queue in batches. Preserve all turns.
        return {"_single_card_batch": items}

    def merge_requests(self, requests):
        return {"_single_card_batch": requests}

    async def _consume_one_request(self, payload):
        if isinstance(payload, dict) and "_single_card_batch" in payload:
            for item in payload["_single_card_batch"]:
                await self._consume_one_request(item)
            return
        meta = payload.get("meta", {}) if isinstance(payload, dict) else value(payload, "channel_meta", {}) or {}
        message_id = str(meta.get("message_id") or "")
        if message_id:
            old = self.store.get(identity(self.agent_id + self.client_id, message_id))
            if old and old.status in TERMINAL:
                self._release_message_ids([message_id])
                return
        try:
            await super()._consume_one_request(payload)
        except asyncio.CancelledError:
            await self.cancel_turn(self.find_turn(meta=meta))
            raise
        finally:
            self._release_message_ids([message_id])

    async def cancel_turn(self, turn):
        if turn and turn.status not in TERMINAL:
            turn.status, turn.error = "cancelled", "本次任务已停止。"
            turn.ended = time.time()
            turn.answer = turn.error
            for approval in turn.pending():
                approval["status"] = "expired"
                self.sync_approval_step(turn, approval)
            turn.touch()
            self.store.save(turn)
            try:
                await asyncio.wait_for(self.flush(turn), timeout=3)
            except Exception:
                logger.warning("Cancelled card persisted; remote update pending: %s", turn.id)

    async def _stream_with_tracker(self, payload):
        try:
            async for event in super()._stream_with_tracker(payload):
                yield event
        except asyncio.CancelledError:
            meta = payload.get("meta", {}) if isinstance(payload, dict) else value(payload, "channel_meta", {})
            await self.cancel_turn(self.find_turn(meta=meta))
            raise

    @property
    def agent_id(self):
        return str(getattr(self._workspace, "agent_id", None) or "default")

    def find_turn(self, request=None, meta=None, session_id="", user_id=""):
        key = getattr(request, "_dingtalk_ai_turn", None) if request else None
        if key:
            return self.turns.get(key) or self.store.get(key)
        meta = meta or {}
        message_id = meta.get("message_id")
        if message_id:
            key = identity(self.agent_id + self.client_id, str(message_id))
            found = self.turns.get(key) or self.store.get(key)
            if found:
                return found
        session_id = session_id or value(request, "session_id", "") or meta.get("session_id", "")
        user_id = user_id or value(request, "user_id", "") or meta.get("user_id", "")
        found = self.store.latest(session_id, user_id)
        return self.turns.get(found.id, found) if found else None

    async def _before_consume_process(self, request):
        meta = getattr(request, "channel_meta", None) or {}
        message_id = str(meta.get("message_id") or uuid.uuid4().hex)
        key = identity(self.agent_id + self.client_id, message_id)
        setattr(request, "_dingtalk_ai_turn", key)
        existing = self.turns.get(key) or self.store.get(key)
        if existing:
            self.turns[key] = existing
            return
        turn = Turn(key, request.session_id, request.user_id,
            str(meta.get("sender_staff_id") or ""), str(meta.get("conversation_id") or ""),
            agent_id=self.agent_id, is_group=bool(meta.get("is_group")), message_id=str(meta.get("message_id") or ""))
        self.turns[key] = turn
        self.store.save(turn)
        await self.flush(turn)

    async def flush(self, turn):
        async with self.locks.setdefault(turn.id, asyncio.Lock()):
            await self.sync_reaction(turn)
            data = self.card_view(turn)
            final = turn.status in TERMINAL
            self.store.save(turn)
            if not turn.delivered:
                await self.transport.create(turn, data)
                turn.delivered = True
                self.store.save(turn)
            await self.transport.update(turn, data)
            if not turn.finalized:
                content = data["content"] or data["status"]
                if final or self.last_stream.get(turn.id) != content:
                    await self.transport.stream(turn, content, final=final)
                    self.last_stream[turn.id] = content
                if final:
                    turn.finalized = True
                    self.store.save(turn)
            self.last_flush[turn.id] = time.monotonic()
            self.schedule_tops()

    def card_view(self, turn, *, top=False):
        data = project(turn, page_bytes=self.page_bytes)
        if top:
            return {key: value for key, value in data.items() if key in APPROVAL_FIELDS}
        # Also clears approvals on previously imported main-card templates.
        for key in APPROVAL_FIELDS:
            data[key] = "[]" if key in {"approvalButtons", "approvalAllow", "approvalDeny", "approvalPages"} else "no" if key.startswith("has") else ""
        data["approvalNotice"] = turn.top_error
        if not turn.top_error and turn.pending() and turn.status not in TERMINAL:
            data["approvalNotice"] = "等待你的确认，请在会话顶部处理；多项请求将依次显示。"
        return data

    def schedule_tops(self):
        if self.stopping:
            return
        self.top_event.set()
        if self.top_task and not self.top_task.done():
            return
        async def worker():
            while not self.stopping:
                self.top_event.clear()
                try:
                    active = await self.sync_tops()
                except Exception:
                    logger.exception("Approval ceiling reconciliation failed")
                    active = True
                if not active and not self.top_event.is_set():
                    return
                try:
                    await asyncio.wait_for(self.top_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
        self.top_task = asyncio.create_task(worker())

    async def sync_tops(self):
        """Serialize the visible request per conversation/recipient; retry cleanup.

        Persist intent before network I/O. Ten-minute server expiry bounds stale
        ceilings after crashes; live requests renew their lease every five minutes.
        """
        async with self.top_lock:
            known = {t.id: t for t in self.store.approval_turns()}
            known.update(self.turns)
            turns = sorted(known.values(), key=lambda t: (t.created, t.id))
            selected = {}
            for t in turns:
                if not self.stopping and t.delivered and t.pending() and t.status not in TERMINAL:
                    selected.setdefault((t.conversation_id, t.staff_id), t.id)
            active = False
            blocked = set()
            for t in sorted(turns, key=lambda t: selected.get((t.conversation_id, t.staff_id)) == t.id):
                key = (t.conversation_id, t.staff_id)
                wanted = selected.get(key) == t.id
                old_error = t.top_error
                try:
                    if t.top_active and not wanted:
                        # Clear controls before closure, including delayed/retried closes.
                        if t.top_expires > time.time():
                            if t.top_created:
                                try:
                                    await self.transport.update_top(t, self.card_view(t, top=True))
                                except Exception:
                                    logger.warning("Could not clear ceiling before close: %s", t.id)
                            await self.transport.close_top(t)
                        t.top_active = False
                        self.top_views.pop(t.id, None)
                    if wanted:
                        active = True
                        if key in blocked:
                            continue
                        if not self.top_template_id:
                            raise ValueError("请在钉钉 AI 设置中填写审批吊顶模板 ID；本次操作尚未获准执行。")
                        data = self.card_view(t, top=True)
                        fingerprint = json.dumps(data, sort_keys=True)
                        if not t.top_active or t.top_expires < time.time() + 300 or not t.top_created:
                            t.top_active = True
                            t.top_expires = time.time() + 600
                            self.store.save(t)
                            await self.transport.open_top(t, data, self.top_template_id)
                            t.top_created = True
                            self.top_views[t.id] = fingerprint
                        elif self.top_views.get(t.id) != fingerprint:
                            await self.transport.update_top(t, data)
                            self.top_views[t.id] = fingerprint
                    t.top_error = ""
                except Exception as exc:
                    active = True
                    blocked.add(key)
                    t.top_error = (str(exc) if isinstance(exc, ValueError) else
                        "审批吊顶暂时无法显示或关闭，正在重试。请检查吊顶模板、应用权限与会话支持情况；尚未批准的操作不会执行。")
                    logger.warning("Approval ceiling sync failed for %s: %s", t.id, type(exc).__name__)
                self.store.save(t)
                if old_error != t.top_error and not self.stopping:
                    self.changed(t)
            return active or any(t.top_active for t in turns)

    async def sync_reaction(self, turn):
        """Reuse the official best-effort reaction API on the inbound message.

        The official helper swallows API errors, so `reaction` records the
        last requested state, not a delivery acknowledgement.
        """
        if not turn.message_id or not turn.conversation_id:
            return
        target = {"running": "🤔Thinking", "waiting": "⏳待确认", "completed": "🥳Done",
                  "failed": "☹️Error", "cancelled": "🛑已停止", "interrupted": "☹️Error"}.get(turn.status)
        if not target or target == turn.reaction:
            return
        try:
            if turn.reaction:
                await asyncio.wait_for(self._send_emotion(turn.message_id, turn.conversation_id,
                    turn.reaction, recall=True), timeout=2)
            await asyncio.wait_for(self._send_emotion(turn.message_id, turn.conversation_id, target), timeout=2)
            turn.reaction = target
            self.store.save(turn)
        except Exception:
            # Feedback must never prevent the card or the agent from running.
            logger.debug("Message reaction update failed for %s", turn.id, exc_info=True)

    def changed(self, turn):
        turn.touch()
        self.store.save(turn)
        if turn.top_active or turn.pending():
            self.schedule_tops()
        task = self.flush_tasks.get(turn.id)
        if task and not task.done():
            return
        async def worker():
            try:
                while True:
                    await asyncio.sleep(max(0.2, 0.8 - (time.monotonic() - self.last_flush.get(turn.id, 0))))
                    revision = turn.revision
                    await self.flush(turn)
                    if revision == turn.revision:
                        break
            except Exception:
                logger.exception("AI card update failed for %s", turn.id)
        self.flush_tasks[turn.id] = asyncio.create_task(worker())

    async def on_streaming_start(self, request, to_handle, event, send_meta, stream_type, accumulated_text=""):
        await self.on_streaming_delta(request, to_handle, event, send_meta, stream_type, accumulated_text)

    async def on_streaming_delta(self, request, to_handle, event, send_meta, stream_type, accumulated_text=""):
        turn = self.find_turn(request, send_meta)
        if not turn or turn.status in TERMINAL:
            return
        key = str(value(event, "msg_id", None) or value(event, "id", None) or value(event, "message_id", None) or stream_type)
        if stream_type == "reasoning":
            turn.step(key, "reasoning", "思考过程").content = accumulated_text
        else:
            turn.set_answer(key, accumulated_text)
        self.changed(turn)

    async def on_streaming_end(self, request, to_handle, event, send_meta, stream_type, accumulated_text=""):
        await self.on_streaming_delta(request, to_handle, event, send_meta, stream_type, accumulated_text)
        # Finalize only in _on_process_completed, never at a segment boundary.

    def capture_tool(self, turn, data, event, completed=True):
        if not isinstance(data, dict) or not any(k in data for k in ("arguments", "output")):
            return False
        msg_id = str(value(event, "msg_id", None) or value(event, "id", ""))
        mapping = self.tool_message_ids.setdefault(turn.id, {})
        key = str(data.get("call_id") or data.get("tool_call_id") or data.get("id") or mapping.get(msg_id, "") or msg_id)
        if msg_id and key:
            mapping[msg_id] = key
        existing = next((s for s in turn.steps if s.id == key), None)
        name = str(data.get("name") or (existing.name if existing else "工具"))
        if not key:
            key = name
        # Output events may omit call_id. Match the latest unresolved call.
        if "output" in data and not any(s.id == key for s in turn.steps):
            match = next((s for s in reversed(turn.steps) if s.kind == "tool" and s.name == name and s.status == "running"), None)
            if match:
                key = match.id
        args = data.get("arguments", "")
        if "arguments" in data and not completed and value(event, "delta", False) and existing:
            args = existing.arguments + text(args)
        parsed = args
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except (ValueError, TypeError):
                pass
        command = next((parsed[k] for k in ("command", "cmd", "code") if isinstance(parsed, dict) and parsed.get(k)), "")
        step = turn.step(key, "tool", text(command)[:300] or name)
        step.name = name
        if command:
            step.title = text(command)[:300]
        if "arguments" in data:
            step.arguments = text(args)
        if "output" in data:
            output = text(data["output"])
            if completed:
                step.content = output
                if step.status not in {"denied", "timeout", "expired"}:
                    step.status = tool_result_status(data)
            else:
                step.content = step.content + output if value(event, "delta", False) else output
        self.changed(turn)
        return True

    async def on_event_content(self, request, to_handle, event, send_meta):
        turn = self.find_turn(request, send_meta)
        if turn and turn.status not in TERMINAL:
            return self.capture_tool(turn, value(event, "data"), event,
                                     completed=value(event, "status") == "completed")
        return True

    async def on_event_message_completed(self, request, to_handle, event, send_meta):
        turn = self.find_turn(request, send_meta)
        if not turn or turn.status in TERMINAL:
            return
        parts = value(event, "content", []) or []
        tool = False
        for part in parts:
            tool = self.capture_tool(turn, value(part, "data"), event) or tool
        if not tool:
            body = "".join(value(p, "text", "") or value(p, "refusal", "") or "" for p in parts)
            if body:
                if value(event, "type") == "reasoning":
                    turn.step(str(value(event, "id", None) or "reasoning"), "reasoning", "思考过程").content = body
                else:
                    turn.set_answer(str(value(event, "id", None) or "answer"), body)
                self.changed(turn)

    async def _on_process_completed(self, request, to_handle, send_meta):
        turn = self.find_turn(request, send_meta)
        if not turn:
            return
        if turn.status not in TERMINAL:
            turn.status = "completed"
        turn.ended = turn.ended or time.time()
        for step in turn.steps:
            if step.status == "running":
                step.status = ("unknown" if step.kind == "tool" else "completed") if turn.status == "completed" else "interrupted"
        for pending in turn.pending():
            pending["status"] = "expired"
            self.sync_approval_step(turn, pending)
        if not turn.answer and not turn.error:
            turn.answer = "本次处理已完成。"
        turn.touch()
        task = self.flush_tasks.pop(turn.id, None)
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await self.flush(turn)
        self.turns.pop(turn.id, None)
        self.tool_message_ids.pop(turn.id, None)
        self.locks.pop(turn.id, None)
        self.last_flush.pop(turn.id, None)
        self.last_stream.pop(turn.id, None)
        await self.sync_tops()
        self.schedule_tops()
        self.store.prune(self.retention_days)

    async def _on_consume_error(self, request, to_handle, err_text):
        turn = self.find_turn(request)
        if turn:
            turn.status, turn.error = "failed", err_text
            turn.answer = (turn.answer + "\n\n" if turn.answer else "") + "本次执行失败。\n\n" + err_text
            await self._on_process_completed(request, to_handle, {})

    async def _send_model_fallback_notice(self, to_handle, event, meta):
        return

    async def send_content_parts(self, to_handle, parts, meta=None):
        await self.send(to_handle, "\n".join(value(p, "text", "") or "" for p in parts), meta)

    async def send(self, to_handle, text, meta=None):
        turn = self.find_turn(meta=meta)
        if not turn:
            raise RuntimeError("AI 单卡渠道需要原始消息上下文，不支持无目标的主动推送")
        turn.answer = text
        self.changed(turn)

    async def send_approval_notification(self, *, session_id, user_id, request_id, tool_name,
                                         severity, result_summary, channel_meta=None):
        from qwenpaw.app.approvals.service import get_approval_service
        turn = self.find_turn(meta=channel_meta, session_id=session_id, user_id=user_id)
        if not turn or turn.status in TERMINAL:
            raise RuntimeError("审批没有对应的活动卡片")
        turn.status = "waiting"
        pending = await get_approval_service().get_request(request_id)
        extra = getattr(pending, "extra", {}) if pending else {}
        extra = extra if isinstance(extra, dict) else {}
        call = extra.get("tool_call") or {}
        args = call.get("input") if isinstance(call, dict) else None
        if not isinstance(args, dict):
            args = extra.get("input")
        if not isinstance(args, dict):
            args = {k: extra[k] for k in ("command", "cwd", "permissions", "blocked_path") if extra.get(k)}
        turn.approvals[request_id] = {"id": request_id, "title": f"审批 · {tool_name} · {severity}",
            "summary": result_summary, "status": "pending", "tool_name": tool_name, "arguments": args,
            "call_id": str(call.get("id") or extra.get("provider_item_id") or "") if isinstance(call, dict) else ""}
        self.sync_approval_step(turn, turn.approvals[request_id])
        turn.step("approval:" + request_id, "approval", f"审批详情 · {tool_name}").content = result_summary
        self.changed(turn)
        if pending:
            async def watch():
                try:
                    decision = await asyncio.shield(pending.future)
                    turn.approvals[request_id]["status"] = decision.value
                    self.sync_approval_step(turn, turn.approvals[request_id])
                    if turn.status not in TERMINAL:
                        turn.status = "waiting" if turn.pending() else "running"
                    self.changed(turn)
                except asyncio.CancelledError:
                    pass
            task = asyncio.create_task(watch())
            self.watchers.add(task)
            task.add_done_callback(self.watchers.discard)

    @staticmethod
    def sync_approval_step(turn, approval):
        # Never correlate by tool name: concurrent calls may share a name.
        key = approval.get("call_id")
        if not key:
            return
        step = turn.step(key, "tool", approval.get("tool_name", "工具"))
        step.name = approval.get("tool_name", "工具")
        step.arguments = text(approval.get("arguments", {}))
        status = approval["status"]
        if status in {"pending", "denied", "timeout", "expired"}:
            step.status = {"pending": "waiting", "denied": "denied", "timeout": "timeout", "expired": "expired"}[status]
        elif status == "approved" and step.status == "waiting":
            step.status = "running"

    @staticmethod
    def callback_response(public=None, private=None, *, success=True):
        result = {"cardUpdateOptions": {"updateCardDataByKey": True, "updatePrivateDataByKey": True}}
        private = dict(private or {})
        if public is not None:
            public = dict(public)
            public.pop("content", None)  # Streaming API owns active answer text.
            for key in PRIVATE_FIELDS & public.keys():
                private[key] = public.pop(key)
            result["cardData"] = {"cardParamMap": public}
        result["userPrivateData"] = {"cardParamMap": {
            **(private or {}), "actionResult": "ok" if success else "error"}}
        return result

    async def card_callback(self, payload):
        turn = None
        try:
            card_id = str(payload.get("outTrackId") or "")
            is_top = card_id.endswith("_approval")
            turn_id = card_id[:-9] if is_top else card_id
            turn = self.turns.get(turn_id) or self.store.get(turn_id)
            if not turn or turn.agent_id != self.agent_id:
                raise ValueError("记录已过期或不属于当前智能体")
            content = payload.get("content") or {}
            if isinstance(content, str):
                content = json.loads(content)
            params = content.get("cardPrivateData", {}).get("params", {})
            if params.get("turn_id") != turn.id:
                raise ValueError("卡片与请求不匹配")
            if not turn.staff_id or str(payload.get("userId") or "") != turn.staff_id:
                raise ValueError("只有发起本轮对话的用户可以操作此卡片")
            action = params.get("action")
            if is_top and (card_id != self.transport.top_id(turn) or not turn.top_active):
                raise ValueError("审批吊顶已关闭")
            if action in {"approve", "deny", "approval_page"} and not is_top:
                raise ValueError("请在会话顶部审批，原消息内的审批入口已停用")
            if is_top and action not in {"approve", "deny", "approval_page", "close"}:
                raise ValueError("此操作不属于审批吊顶")
            if action == "thought_toggle":
                key = str(params.get("step_id") or "")
                if not any(s.id == key and s.kind in {"reasoning", "progress"} for s in turn.steps):
                    raise ValueError("思考记录不存在或已过期")
                turn.view_pages["_thought:" + key] = 1 if str(params.get("page")) == "1" else 0
                self.store.save(turn)
                return self.callback_response(public=self.card_view(turn, top=is_top))
            if action == "approval_page":
                approval_id = str(params.get("approval_id") or "")
                current = next(iter(turn.pending()), None) if turn.status not in TERMINAL else None
                if not current or current["id"] != approval_id:
                    raise ValueError("审批内容已经更新，请查看当前操作")
                turn.view_pages["approval:" + approval_id] = max(0, int(params.get("page", 0)))
                self.store.save(turn)
                return self.callback_response(public=self.card_view(turn, top=is_top))
            if action in {"inline_page", "process_page"}:
                key = str(params.get("step_id") or "") if action == "inline_page" else "_process"
                if action == "inline_page" and not any(s.id == key for s in turn.steps):
                    raise ValueError("过程记录不存在或已过期")
                turn.view_pages[key] = max(0, int(params.get("page", 0)))
                self.store.save(turn)
                return self.callback_response(public=self.card_view(turn, top=is_top))
            if action in {"approve", "deny"}:
                from qwenpaw.app.approvals.service import get_approval_service
                from qwenpaw.security.tool_guard.approval import ApprovalDecision, ApprovalScope
                approval_id = str(params.get("approval_id") or "")
                approval = turn.approvals.get(approval_id)
                current = next(iter(turn.pending()), None)
                if not current or current["id"] != approval_id:
                    raise ValueError("审批已过期或不是当前待确认操作")
                pending = await get_approval_service().get_request(approval_id)
                if (not approval or approval["status"] != "pending" or turn.status in TERMINAL
                    or not pending or pending.channel != self.channel or pending.user_id != turn.user_id
                    or pending.owner_agent_id != turn.agent_id
                    or turn.session_id not in {pending.session_id, pending.root_session_id}):
                    raise ValueError("审批已处理、已过期或不属于本轮对话")
                decision = ApprovalDecision.APPROVED if action == "approve" else ApprovalDecision.DENIED
                resolved = await get_approval_service().resolve_request(approval_id, decision, scope=ApprovalScope.EXACT)
                if resolved is None:
                    raise ValueError("审批已由其他入口处理")
                approval["status"] = decision.value
                self.sync_approval_step(turn, approval)
                turn.status = "waiting" if turn.pending() else "running"
                turn.touch()
                self.store.save(turn)
                self.changed(turn)
                self.schedule_tops()
                return self.callback_response(public=self.card_view(turn, top=is_top), private={"detailVisible": "no"})
            if action == "close":
                return self.callback_response(private={"detailVisible": "no"})
            if action not in {"history", "step", "answer"}:
                raise ValueError("不支持的卡片操作")
            return self.callback_response(private=detail(turn, action, int(params.get("page", 0)),
                str(params.get("step_id", "")), self.page_bytes))
        except (ValueError, TypeError, KeyError) as exc:
            return self.callback_response(success=False, private={"detailVisible": "yes", "detailTitle": "无法操作",
                "detailBody": str(exc), "detailButtons": "[]", "detailEpoch": "done" if turn and turn.status in TERMINAL else "active"})

    def _run_stream_forever(self):
        self._client.register_callback_handler("/v1.0/card/instances/callback", CardCallback(self))
        super()._run_stream_forever()

    async def _recover_active_cards(self):
        # Our store is independent of the built-in channel's card store.
        for turn in self.store.recent(10000):
            if turn.status not in TERMINAL:
                turn.status, turn.error = "interrupted", "服务重启，请重新发送消息。"
                turn.ended = time.time()
                for approval in turn.pending():
                    approval["status"] = "expired"
                    self.sync_approval_step(turn, approval)
                turn.touch()
                self.store.save(turn)
            if turn.delivered and not turn.finalized:
                try:
                    await self.flush(turn)
                except Exception:
                    logger.exception("Could not mark interrupted card %s", turn.id)
        await self.sync_tops()
        self.schedule_tops()
        self.store.prune(self.retention_days)

    async def start(self):
        if not self.enabled:
            return
        if not self.card_template_id:
            raise ValueError("请先导入并发布 AI 卡片模板，填写模板 ID")
        await self.transport.start()
        try:
            await super().start()
        except Exception:
            await self.transport.close()
            raise

    async def stop(self):
        self.stopping = True
        if self.top_task:
            self.top_task.cancel()
            await asyncio.gather(self.top_task, return_exceptions=True)
        for task in [*self.flush_tasks.values(), *self.watchers]:
            task.cancel()
        await asyncio.gather(*self.flush_tasks.values(), *self.watchers, return_exceptions=True)
        if self.enabled:
            for turn in self.turns.values():
                if turn.status not in TERMINAL:
                    turn.status, turn.error = "interrupted", "渠道已停止，请重新发送消息。"
                    turn.ended = time.time()
                    for approval in turn.pending():
                        approval["status"] = "expired"
                        self.sync_approval_step(turn, approval)
                    turn.touch()
                    self.store.save(turn)
                    try:
                        await self.flush(turn)
                    except Exception:
                        logger.exception("Could not finalize card on stop")
        await self.sync_tops()
        await super().stop()
        await self.transport.close()
        self.store.close()
