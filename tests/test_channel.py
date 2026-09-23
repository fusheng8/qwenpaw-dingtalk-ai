import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from qpai.channel import DingTalkAIChannel
from qpai.state import Turn, project
from qpai.transport import CardTransport


@pytest.fixture
def channel(tmp_path):
    async def process(request):
        if False:
            yield None
    ch = DingTalkAIChannel.from_config(process, NS(enabled=False, client_id="test", client_secret="not-a-secret",
        card_template_id="template", top_template_id="top-template"), workspace_dir=tmp_path)
    ch.transport = NS(create=AsyncMock(), update=AsyncMock(), stream=AsyncMock(), close=AsyncMock(), open_top=AsyncMock(), update_top=AsyncMock(), close_top=AsyncMock(), top_id=CardTransport.top_id)
    ch.schedule_tops = lambda: None  # Reconcile explicitly for deterministic lifecycle tests.
    ch._send_emotion = AsyncMock()
    yield ch
    ch.store.close()


def request(message="m1"):
    return NS(session_id="session", user_id="user", channel_meta={"message_id": message,
        "sender_staff_id": "staff", "conversation_id": "conversation", "is_group": False})


def callback(turn, action, **params):
    return {"outTrackId": turn.id + ("_approval" if action in {"approve", "approve_similar", "deny", "cancel_approval", "approval_page"} else ""), "userId": "staff", "content": json.dumps({"cardPrivateData": {
        "params": {"turn_id": turn.id, "action": action, **params}}})}


@pytest.mark.asyncio
async def test_reactions_follow_original_message_and_replace_previous_state(channel):
    req = request("original-message")
    await channel._before_consume_process(req)
    t = channel.find_turn(req)
    channel._send_emotion.assert_awaited_once_with("original-message", "conversation", "🤔Thinking")
    await channel.flush(t)
    assert channel._send_emotion.await_count == 1
    t.status = "waiting"
    await channel.flush(t)
    t.status = "running"
    await channel.flush(t)
    await channel._on_process_completed(req, "", {})
    calls = channel._send_emotion.await_args_list
    assert [(c.args[2], c.kwargs.get("recall", False)) for c in calls] == [
        ("🤔Thinking", False), ("🤔Thinking", True), ("⏳待确认", False),
        ("⏳待确认", True), ("🤔Thinking", False), ("🤔Thinking", True), ("🥳Done", False)]
    assert all(c.args[:2] == ("original-message", "conversation") for c in calls)
    restored = channel.store.get(t.id)
    assert restored.message_id == "original-message" and restored.reaction == "🥳Done"
    await channel.sync_reaction(restored)
    assert channel._send_emotion.await_count == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("status,emoji", [("failed", "☹️Error"), ("cancelled", "🛑已停止"), ("interrupted", "☹️Error")])
async def test_terminal_reactions_never_show_done_on_error(channel, status, emoji):
    t = Turn("t", "s", "u", "staff", "c", status=status, message_id="m", reaction="🤔Thinking")
    await channel.sync_reaction(t)
    assert channel._send_emotion.await_args.args == ("m", "c", emoji)
    assert t.reaction == emoji


@pytest.mark.asyncio
async def test_reaction_failure_does_not_block_card_and_missing_id_is_skipped(channel):
    channel._send_emotion.side_effect = RuntimeError("API unavailable")
    req = request()
    await channel._before_consume_process(req)
    assert channel.transport.create.await_count == 1
    t = channel.find_turn(req)
    assert t.reaction == ""
    channel._send_emotion.reset_mock()
    t.message_id = ""
    await channel.sync_reaction(t)
    channel._send_emotion.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_call_cannot_be_overwritten_by_output_completion(channel):
    req = request(); await channel._before_consume_process(req)
    t = channel.find_turn(req)
    approval = {"call_id": "call", "tool_name": "shell", "arguments": {"command": "test"}, "status": "pending"}
    channel.sync_approval_step(t, approval)
    assert t.steps[0].status == "waiting"
    approval["status"] = "denied"
    channel.sync_approval_step(t, approval)
    channel.capture_tool(t, {"call_id": "call", "name": "shell", "output": {"exit_code": 0}}, NS(id="out"))
    assert t.steps[0].status == "denied"
    assert "未执行" in json.loads(project(t)["processRows"])[0]["sheetBody"]
    channel.capture_tool(t, {"call_id": "other", "name": "shell", "arguments": {"cmd": "other"}}, NS(id="other"))
    await channel._on_process_completed(req, "", {})
    assert next(s for s in t.steps if s.id == "other").status == "unknown"


@pytest.mark.asyncio
async def test_thought_expand_updates_same_card_without_rewriting_stream(channel):
    req = request(); await channel._before_consume_process(req)
    t = channel.find_turn(req)
    t.step("r", "reasoning", "思考").content = "完整思考" * 400
    t.answer = "当前答案"
    await channel.flush(t)
    before = channel.transport.stream.await_count
    await channel.flush(t)
    assert channel.transport.stream.await_count == before
    response = await channel.card_callback(callback(t, "thought_toggle", step_id="r", page=1))
    assert "content" not in response["cardData"]["cardParamMap"]
    row = json.loads(response["userPrivateData"]["cardParamMap"]["processRows"])[0]
    assert len(row["thoughtText"]) == 221
    assert row["sheetBody"] == t.steps[0].content
    assert channel.transport.create.await_count == 1


@pytest.mark.asyncio
async def test_one_card_across_reasoning_tools_answer_and_finalization(channel):
    ch, req = channel, request()
    await ch._before_consume_process(req)
    turn = ch.find_turn(req)
    await ch.on_streaming_delta(req, "", NS(id="reason"), {}, "reasoning", "思考内容")
    await ch.on_streaming_end(req, "", NS(id="reason"), {}, "reasoning", "思考内容")
    assert not turn.finalized
    await ch.on_event_message_completed(req, "", NS(id="call-event", content=[NS(data={"name": "shell",
        "call_id": "call", "arguments": '{"command":"ls -la"}'})]), {})
    await ch.on_event_message_completed(req, "", NS(id="result-event", content=[NS(data={"name": "shell",
        "call_id": "call", "output": "完整输出" * 5000})]), {})
    await ch.on_streaming_end(req, "", NS(id="answer"), {}, "message", "最终答复")
    await ch._on_process_completed(req, "", {})
    assert ch.transport.create.await_count == 1
    assert sum(bool(c.kwargs.get("final")) for c in ch.transport.stream.await_args_list) == 1
    saved = ch.store.get(turn.id)
    assert saved.answer == "最终答复" and saved.status == "completed"
    assert next(s for s in saved.steps if s.id == "call").content == "完整输出" * 5000
    assert next(s for s in saved.steps if s.id == "call").title == "ls -la"
    view = await ch.card_callback(callback(saved, "step", step_id="call", page=1))
    assert view["userPrivateData"]["cardParamMap"]["detailVisible"] == "yes"


@pytest.mark.asyncio
async def test_callbacks_reject_other_users_and_wrong_turn(channel):
    req = request(); await channel._before_consume_process(req)
    turn = channel.find_turn(req)
    event = callback(turn, "history"); event["userId"] = "someone-else"
    result = await channel.card_callback(event)
    assert "只有发起" in result["userPrivateData"]["cardParamMap"]["detailBody"]
    assert result["userPrivateData"]["cardParamMap"]["actionResult"] == "error"
    event = callback(turn, "approve", approval_id="unknown")
    result = await channel.card_callback(event)
    assert "已关闭" in result["userPrivateData"]["cardParamMap"]["detailBody"]
    assert result["userPrivateData"]["cardParamMap"]["actionResult"] == "error"
    success = await channel.card_callback(callback(turn, "close"))
    assert success["userPrivateData"]["cardParamMap"]["actionResult"] == "ok"


@pytest.mark.asyncio
async def test_batch_is_processed_as_separate_turns(channel, monkeypatch):
    from qwenpaw.app.channels.dingtalk.channel import DingTalkChannel
    process = AsyncMock()
    monkeypatch.setattr(DingTalkChannel, "_consume_one_request", process)
    items = [{"content_parts": [], "meta": {"message_id": "a"}}, {"content_parts": [], "meta": {"message_id": "b"}}]
    await channel._consume_one_request(channel.merge_native_items(items))
    assert process.await_count == 2
    assert [c.args[0] for c in process.await_args_list] == items


@pytest.mark.asyncio
async def test_approval_resolves_exact_request_once(channel, monkeypatch):
    from qwenpaw.app.approvals import service
    from qwenpaw.security.tool_guard.approval import ApprovalDecision, ApprovalScope
    req = request(); await channel._before_consume_process(req)
    turn = channel.find_turn(req)
    pending = NS(channel=channel.channel, user_id="user", owner_agent_id="default", session_id="session",
        root_session_id="session", future=asyncio.get_running_loop().create_future(),
        extra={"tool_call": {"id": "call1", "input": {"command": "python report.py", "cwd": "/data"}},
               "channel_meta": {"session_webhook": "must not copy"}})
    fake = NS(get_request=AsyncMock(return_value=pending), resolve_request=AsyncMock(return_value=pending))
    monkeypatch.setattr(service, "get_approval_service", lambda: fake)
    await channel.send_approval_notification(session_id="session", user_id="user", request_id="approval1",
        tool_name="shell", severity="high", result_summary="运行部署命令")
    await channel.sync_tops()
    assert turn.approvals["approval1"]["arguments"] == {"command": "python report.py", "cwd": "/data"}
    page = await channel.card_callback(callback(turn, "approval_page", approval_id="approval1", page="0"))
    assert page["userPrivateData"]["cardParamMap"]["approvalTarget"] == "python report.py"
    assert "approvalTarget" not in page["cardData"]["cardParamMap"]
    wrong = await channel.card_callback(callback(turn, "approval_page", approval_id="wrong", page="0"))
    assert wrong["userPrivateData"]["cardParamMap"]["actionResult"] == "error"
    response = await channel.card_callback(callback(turn, "approve", approval_id="approval1"))
    assert response["userPrivateData"]["cardParamMap"]["hasApproval"] == "no"
    assert response["userPrivateData"]["cardParamMap"]["approvalBody"] == ""
    assert response["userPrivateData"]["cardParamMap"]["approvalAllow"] == "[]"
    await channel.card_callback(callback(turn, "approve", approval_id="approval1"))
    fake.resolve_request.assert_awaited_once_with("approval1", ApprovalDecision.APPROVED, scope=ApprovalScope.EXACT)
    assert not turn.pending()
    pending.future.set_result(ApprovalDecision.APPROVED)
    await asyncio.gather(*channel.watchers)
    await asyncio.gather(*channel.flush_tasks.values())


@pytest.mark.asyncio
async def test_recovery_invalidates_approvals_and_finalizes_same_card(channel):
    t = Turn("old", "s", "u", "staff", "c", delivered=True)
    t.approvals["old-approval"] = {"id": "old-approval", "status": "pending", "title": "审批", "summary": "旧请求"}
    channel.store.save(t)
    await channel._recover_active_cards()
    t = channel.store.get("old")
    assert t.status == "interrupted" and not t.pending() and t.finalized
    channel.transport.create.assert_not_awaited()


def test_native_request_routes_to_plugin_and_isolates_users(channel):
    req = channel.build_agent_request_from_native({"channel_id": "dingtalk", "sender_id": "u", "content_parts": [],
        "meta": {"conversation_id": "c"}})
    assert req.channel == "dingtalk_ai"
    assert channel.resolve_session_id("u1", {"conversation_id": "c"}) != channel.resolve_session_id("u2", {"conversation_id": "c"})


@pytest.mark.asyncio
async def test_real_envelope_argument_deltas_and_cumulative_output(channel):
    from qwenpaw.schemas import DataContent, TextContent, Message, MessageType, RunStatus
    ch, req = channel, request()
    await ch._before_consume_process(req)
    turn = ch.find_turn(req)
    events = [
        DataContent(msg_id="m-tool", delta=True, data={"call_id": "call", "name": "shell", "arguments": ""}),
        DataContent(msg_id="m-tool", delta=True, data={"arguments": '{"command":'}),
        DataContent(msg_id="m-tool", delta=True, data={"arguments": '"ls"}'}),
        DataContent(msg_id="m-out", delta=False, data={"call_id": "call", "name": "shell", "output": "one"}),
        DataContent(msg_id="m-out", delta=False, data={"call_id": "call", "name": "shell", "output": "one two"}),
    ]
    for event in events:
        event.status = RunStatus.InProgress
        await ch.on_event_content(req, "", event, {})
    assert len(turn.steps) == 1
    assert turn.steps[0].title == "ls"
    assert turn.steps[0].arguments == '{"command":"ls"}'
    assert turn.steps[0].content == "one two"
    msg = Message(id="thought", type=MessageType.REASONING, content=[], status=RunStatus.InProgress)
    delta = TextContent(msg_id="thought", delta=True, text="thinking")
    await ch.on_streaming_start(req, "", msg, {}, "reasoning")
    await ch.on_streaming_delta(req, "", delta, {}, "reasoning", "thinking")
    assert len([s for s in turn.steps if s.kind == "reasoning"]) == 1
    await ch._on_process_completed(req, "", {})


@pytest.mark.asyncio
async def test_actual_base_stream_dispatch_uses_plugin_hooks(channel):
    from qwenpaw.schemas import TextContent, Message, MessageType, RunStatus
    async def process(req):
        for kind, mid, body in [(MessageType.REASONING, "r", "公开思考"), (MessageType.MESSAGE, "a", "回答")]:
            yield Message(object="message", id=mid, type=kind, status=RunStatus.InProgress, content=[])
            yield TextContent(msg_id=mid, delta=True, text=body, status=RunStatus.InProgress)
            yield Message(object="message", id=mid, type=kind, status=RunStatus.Completed, content=[TextContent(text=body)])
    channel._process = process
    payload = {"sender_id": "user", "content_parts": [TextContent(text="hello")], "meta": {
        "message_id": "real-flow", "sender_staff_id": "staff", "conversation_id": "c"}}
    output = [event async for event in channel._stream_with_tracker(payload)]
    assert output
    turn = channel.store.recent()[0]
    assert turn.status == "completed" and turn.answer == "回答"
    assert next(s for s in turn.steps if s.kind == "reasoning").content == "公开思考"
    channel.transport.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_and_cancel_disable_approval(channel):
    req = request(); await channel._before_consume_process(req)
    t = channel.find_turn(req)
    t.approvals["a"] = {"id": "a", "status": "pending", "title": "approval", "summary": "summary"}
    await channel._on_consume_error(req, "", "故障")
    t = channel.store.get(t.id)
    assert t.finalized and t.status == "failed" and not t.pending()
    assert "故障" in t.answer
    req2 = request("m2"); await channel._before_consume_process(req2)
    t2 = channel.find_turn(req2)
    await channel.cancel_turn(t2)
    assert t2.finalized and t2.status == "cancelled"


@pytest.mark.asyncio
async def test_inline_paging_updates_result_in_place_without_new_card(channel):
    req = request(); await channel._before_consume_process(req)
    turn = channel.find_turn(req)
    turn.step("cmd", "tool", "读取数据").content = "甲" * 600 + "乙" * 600
    response = await channel.card_callback(callback(turn, "inline_page", step_id="cmd", page=1))
    rows = json.loads(response["userPrivateData"]["cardParamMap"]["processRows"])
    assert rows[0]["body"] == "乙" * 200
    assert "processRows" not in response["cardData"]["cardParamMap"]
    assert rows[0]["navigation"][0]["action"] == "inline_page"
    channel.transport.create.assert_awaited_once()


def add_top_approval(channel, key="top-turn", approval_id="a", created=1):
    t = Turn(key, "s", "user", "staff", "conversation", delivered=True, status="waiting", created=created)
    t.approvals[approval_id] = {"id": approval_id, "status": "pending", "summary": "请确认这次读取操作", "tool_name": "Bash", "arguments": {"command": "pwd"}}
    channel.turns[t.id] = t
    channel.store.save(t)
    return t


@pytest.mark.asyncio
async def test_top_queue_switches_without_extra_message_and_closes(channel):
    first = add_top_approval(channel, "first", created=1)
    second = add_top_approval(channel, "second", created=2)
    await channel.sync_tops()
    assert first.top_active and not second.top_active
    channel.transport.open_top.assert_awaited_once()
    assert channel.transport.open_top.await_args.args[0].id == first.id
    assert channel.card_view(first)["hasApproval"] == "no"
    assert channel.card_view(first)["approvalAllow"] == "[]"
    first.approvals["a"]["status"] = "denied"
    await channel.sync_tops()
    channel.transport.close_top.assert_awaited_once_with(first)
    assert not first.top_active and second.top_active
    second.status = "cancelled"
    await channel.sync_tops()
    assert not second.top_active
    assert channel.transport.close_top.await_count == 2
    channel.transport.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_next_approval_updates_existing_top_and_unchanged_view_skips_write(channel):
    t = add_top_approval(channel)
    t.approvals["b"] = {**t.approvals["a"], "id": "b"}
    await channel.sync_tops()
    await channel.sync_tops()
    channel.transport.update_top.assert_not_awaited()
    t.approvals["a"]["status"] = "approved"
    await channel.sync_tops()
    channel.transport.open_top.assert_awaited_once()
    channel.transport.close_top.assert_not_awaited()
    data = channel.transport.update_top.await_args.args[1]
    assert json.loads(data["approvalAllow"])[0]["approval_id"] == "b"


@pytest.mark.asyncio
async def test_failed_top_delivery_stays_pending_and_retries(channel):
    t = add_top_approval(channel)
    channel.transport.open_top.side_effect = [RuntimeError("network"), {}]
    await channel.sync_tops()
    assert t.pending() and t.top_error and not t.top_created
    assert channel.card_view(t)["approvalAllow"] == "[]"
    await channel.sync_tops()
    assert t.top_created and not t.top_error
    assert channel.transport.open_top.await_count == 2
    await asyncio.gather(*channel.flush_tasks.values())


@pytest.mark.asyncio
async def test_missing_template_keeps_request_unapproved(channel):
    channel.top_template_id = ""
    t = add_top_approval(channel)
    await channel.sync_tops()
    assert "模板 ID" in channel.card_view(t)["approvalNotice"]
    channel.transport.open_top.assert_not_awaited()
    assert t.pending()
    await asyncio.gather(*channel.flush_tasks.values())


@pytest.mark.asyncio
async def test_close_failure_blocks_next_top_until_retry(channel):
    first = add_top_approval(channel, "first", created=1)
    second = add_top_approval(channel, "second", created=2)
    await channel.sync_tops()
    first.approvals["a"]["status"] = "denied"
    channel.transport.close_top.side_effect = [RuntimeError("network"), {}]
    await channel.sync_tops()
    assert first.top_active and not second.top_active
    await channel.sync_tops()
    assert not first.top_active and second.top_active
    await asyncio.gather(*channel.flush_tasks.values())


@pytest.mark.asyncio
async def test_recovery_closes_persisted_top_even_if_main_card_finalized(channel):
    import time
    t = add_top_approval(channel)
    t.top_active = t.top_created = t.finalized = True
    t.top_expires = time.time() + 600
    channel.store.save(t)
    channel.turns.clear()
    await channel._recover_active_cards()
    restored = channel.store.get(t.id)
    assert restored.status == "interrupted" and not restored.top_active
    channel.transport.close_top.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_card_and_wrong_user_cannot_approve_top(channel, monkeypatch):
    from qwenpaw.app.approvals import service
    fake = NS(get_request=AsyncMock(), resolve_request=AsyncMock())
    monkeypatch.setattr(service, "get_approval_service", lambda: fake)
    t = add_top_approval(channel)
    await channel.sync_tops()
    event = callback(t, "approve", approval_id="a")
    event["outTrackId"] = t.id
    result = await channel.card_callback(event)
    assert result["userPrivateData"]["cardParamMap"]["actionResult"] == "error"
    event["outTrackId"] = t.id + "_approval"
    event["userId"] = "other"
    result = await channel.card_callback(event)
    assert result["userPrivateData"]["cardParamMap"]["actionResult"] == "error"
    fake.resolve_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_top_worker_wakes_for_resolution(channel):
    t = add_top_approval(channel)
    DingTalkAIChannel.schedule_tops(channel)
    for _ in range(20):
        await asyncio.sleep(.01)
        if t.top_created: break
    assert t.top_created
    t.approvals["a"]["status"] = "denied"
    DingTalkAIChannel.schedule_tops(channel)
    await asyncio.wait_for(channel.top_task, timeout=1)
    assert not t.top_active


@pytest.mark.asyncio
async def test_clear_failure_still_attempts_close(channel):
    t = add_top_approval(channel)
    await channel.sync_tops()
    t.approvals["a"]["status"] = "denied"
    channel.transport.update_top.side_effect = RuntimeError("update denied")
    await channel.sync_tops()
    channel.transport.close_top.assert_awaited_once()
    assert not t.top_active


@pytest.mark.asyncio
async def test_expired_server_lease_does_not_block_next_request(channel):
    first = add_top_approval(channel, "first", created=1)
    second = add_top_approval(channel, "second", created=2)
    first.top_active = first.top_created = True
    first.top_expires = 1
    first.status = "interrupted"
    await channel.sync_tops()
    assert not first.top_active and second.top_active
    channel.transport.close_top.assert_not_awaited()


def test_top_detail_contains_full_request_while_preview_remains_single_line(channel):
    t = add_top_approval(channel)
    command = "echo first\necho second\n" + "x" * 2000
    summary = "风险说明\n" * 1000
    t.approvals["a"].update(arguments={"command": command, "cwd": "/example"}, summary=summary)
    view = channel.card_view(t, top=True)
    assert "\n" not in view["approvalTarget"]
    assert summary in view["approvalDetail"]
    assert "x" * 2000 in view["approvalDetail"] and "/example" in view["approvalDetail"]
    assert view["turnId"] == t.id and view["hasApprovalDetail"] == "yes"
    payload = CardTransport.card_data(t, view)
    assert "approvalDetail" not in payload["cardData"]["cardParamMap"]
    assert payload["privateData"]["staff"]["cardParamMap"]["approvalDetail"] == view["approvalDetail"]
    t.approvals["a"]["status"] = "denied"
    view = channel.card_view(t, top=True)
    assert view["hasApproval"] == "no" and view["approvalDetail"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("action,decision,scope", [
    ("approve_similar", "approved", "similar"), ("deny", "denied", "exact"), ("cancel_approval", "denied", "exact")])
async def test_extended_top_approval_actions_use_native_decision_scope(channel, monkeypatch, action, decision, scope):
    from qwenpaw.app.approvals import service
    t = add_top_approval(channel)
    pending = NS(channel=channel.channel, user_id=t.user_id, owner_agent_id=t.agent_id,
                 session_id=t.session_id, root_session_id=t.session_id)
    fake = NS(get_request=AsyncMock(return_value=pending), resolve_request=AsyncMock(return_value=pending))
    monkeypatch.setattr(service, "get_approval_service", lambda: fake)
    await channel.sync_tops()
    response = await channel.card_callback(callback(t, action, approval_id="a"))
    assert response["userPrivateData"]["cardParamMap"]["actionResult"] == "ok"
    call = fake.resolve_request.await_args
    assert call.args[1].value == decision and call.kwargs["scope"].value == scope
    assert t.approvals["a"]["selected_action"] == action
    assert not t.pending()
    await channel.card_callback(callback(t, action, approval_id="a"))
    fake.resolve_request.assert_awaited_once()
    await asyncio.gather(*channel.flush_tasks.values())


def test_channel_default_page_budget_is_three_k(channel):
    assert channel.page_bytes == 3000


def mock_media_delivery(ch, webhook="webhook", webhook_ok=True, api_ok=True):
    ch._get_session_webhook_for_send = AsyncMock(return_value=webhook)
    ch._send_media_part_via_webhook = AsyncMock(return_value=webhook_ok)
    ch._resolve_open_api_params_from_handle = AsyncMock(return_value={
        "conversation_id": "conversation", "conversation_type": "single", "sender_staff_id": "staff"})
    ch._send_media_part_via_open_api = AsyncMock(return_value=api_ok)


@pytest.mark.asyncio
async def test_media_only_parts_are_delivered_without_erasing_answer(channel):
    req = request(); await channel._before_consume_process(req)
    turn = channel.find_turn(req); turn.answer = "已有回答"
    mock_media_delivery(channel)
    parts = [{"type": kind, **fields} for kind, fields in [
        ("image", {"image_url": "https://example.com/a.png"}),
        ("file", {"file_url": "/tmp/a.pdf", "filename": "a.pdf"}),
        ("audio", {"data": "data:audio/amr;base64,YQ=="}),
        ("video", {"video_url": "/tmp/a.mp4"})]]
    await channel.send_content_parts("target", parts, req.channel_meta)
    await channel.send_content_parts("target", parts, req.channel_meta)
    assert channel._send_media_part_via_webhook.await_count == 4
    assert turn.answer == "已有回答"
    assert len(channel.store.get(turn.id).sent_media) == 4
    channel.transport.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_message_delivers_image_and_retains_card_text(channel):
    req = request(); await channel._before_consume_process(req)
    mock_media_delivery(channel)
    await channel.on_event_message_completed(req, "target", NS(id="answer", type="message", content=[
        {"type": "text", "text": "这是图片"},
        {"type": "image", "image_url": "https://example.com/a.png"}]), req.channel_meta)
    assert channel.find_turn(req).answer == "这是图片"
    assert channel._send_media_part_via_webhook.await_args.args[1].image_url.endswith("a.png")
    channel.transport.create.assert_awaited_once()
    task = channel.flush_tasks.get(channel.find_turn(req).id)
    if task: await task


@pytest.mark.asyncio
async def test_media_webhook_failure_falls_back_and_failed_send_can_retry(channel):
    req = request(); await channel._before_consume_process(req)
    mock_media_delivery(channel, webhook_ok=False, api_ok=False)
    parts = [NS(type="file", file_url="/tmp/report.csv", filename="report.csv")]
    with pytest.raises(RuntimeError, match="附件发送失败"):
        await channel.send_content_parts("target", parts, req.channel_meta)
    assert not channel.find_turn(req).sent_media
    channel._send_media_part_via_open_api.return_value = True
    await channel.send_content_parts("target", parts, req.channel_meta)
    assert len(channel.find_turn(req).sent_media) == 1
    assert channel._send_media_part_via_open_api.await_count == 2


@pytest.mark.asyncio
async def test_stream_end_delivers_media_without_second_card(channel):
    req = request(); await channel._before_consume_process(req)
    mock_media_delivery(channel, webhook=None)
    event = NS(id="stream", type="message", content=[NS(type="image", image_url="https://example.com/a.png")])
    await channel.on_streaming_end(req, "target", event, req.channel_meta, "message", "生成完成")
    channel._send_media_part_via_open_api.assert_awaited_once()
    channel.transport.create.assert_awaited_once()
    task = channel.flush_tasks.get(channel.find_turn(req).id)
    if task: await task


@pytest.mark.asyncio
async def test_send_file_tool_output_is_rendered_to_attachment(channel):
    req = request(); await channel._before_consume_process(req)
    mock_media_delivery(channel)
    event = NS(id="tool-file", type="function_call_output", content=[NS(type="data", data={
        "name": "send_file_to_user", "call_id": "send-1", "output": json.dumps([
            {"type": "data", "name": "report.csv", "source": {
                "type": "url", "url": "file:///tmp/report.csv", "media_type": "text/csv"}}])})])
    await channel.on_event_message_completed(req, "target", event, req.channel_meta)
    part = channel._send_media_part_via_webhook.await_args.args[1]
    assert part.file_url == "file:///tmp/report.csv"
    task = channel.flush_tasks.get(channel.find_turn(req).id)
    if task: await task


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["local", "base64", "media_id", "public"])
async def test_images_use_native_messages_not_markdown_or_files(channel, source):
    part = NS(type="image", **{
        "local": {"image_url": "file:///tmp/picture.png"},
        "base64": {"image_url": "data:image/png;base64,aW1hZ2U="},
        "media_id": {"media_id": "@image-id"},
        "public": {"image_url": "https://example.com/picture.png"},
    }[source])
    channel._fetch_bytes_from_url = AsyncMock(return_value=b"image")
    channel._upload_media = AsyncMock(return_value="@image-id")
    channel._send_open_api_message = AsyncMock(return_value=True)
    channel._send_payload_via_session_webhook = AsyncMock(return_value=True)
    ok = await channel._send_media_part_via_webhook("webhook", part)
    if source == "public":
        assert ok
        assert channel._send_payload_via_session_webhook.await_args.args[1]["msgtype"] == "image"
    else:
        assert not ok
        channel._send_payload_via_session_webhook.assert_not_awaited()
    assert await channel._send_media_part_via_open_api(part, "c", "single", "staff")
    kwargs = channel._send_open_api_message.await_args.kwargs
    assert kwargs["msg_key"] == "sampleImageMsg"
    assert kwargs["msg_param"]["photoURL"] == (part.image_url if source == "public" else "@image-id")
    assert channel._upload_media.await_count == (1 if source in {"local", "base64"} else 0)


@pytest.mark.asyncio
async def test_failed_image_upload_does_not_send_empty_image(channel):
    channel._fetch_bytes_from_url = AsyncMock(return_value=b"image")
    channel._upload_media = AsyncMock(return_value=None)
    channel._send_open_api_message = AsyncMock()
    assert not await channel._send_media_part_via_open_api(
        NS(type="image", image_url="file:///tmp/a.png"), "c", "group", "staff")
    channel._send_open_api_message.assert_not_awaited()
