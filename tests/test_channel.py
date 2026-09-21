import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from qpai.channel import DingTalkAIChannel
from qpai.state import Turn, project


@pytest.fixture
def channel(tmp_path):
    async def process(request):
        if False:
            yield None
    ch = DingTalkAIChannel.from_config(process, NS(enabled=False, client_id="test", client_secret="not-a-secret",
        card_template_id="template"), workspace_dir=tmp_path)
    ch.transport = NS(create=AsyncMock(), update=AsyncMock(), stream=AsyncMock(), close=AsyncMock())
    yield ch
    ch.store.close()


def request(message="m1"):
    return NS(session_id="session", user_id="user", channel_meta={"message_id": message,
        "sender_staff_id": "staff", "conversation_id": "conversation", "is_group": False})


def callback(turn, action, **params):
    return {"outTrackId": turn.id, "userId": "staff", "content": json.dumps({"cardPrivateData": {
        "params": {"turn_id": turn.id, "action": action, **params}}})}


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
    assert "已过期" in result["userPrivateData"]["cardParamMap"]["detailBody"]
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
        root_session_id="session", future=asyncio.get_running_loop().create_future())
    fake = NS(get_request=AsyncMock(return_value=pending), resolve_request=AsyncMock(return_value=pending))
    monkeypatch.setattr(service, "get_approval_service", lambda: fake)
    await channel.send_approval_notification(session_id="session", user_id="user", request_id="approval1",
        tool_name="shell", severity="high", result_summary="运行部署命令")
    await channel.card_callback(callback(turn, "approve", approval_id="approval1"))
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
