from unittest.mock import AsyncMock
import pytest
from qpai.transport import CardTransport
from qpai.state import Turn


def test_approval_parameters_stay_private_even_without_staff_id():
    from qpai.state import project, PRIVATE_FIELDS
    t = Turn("t", "s", "u", "staff", "c", is_group=True)
    t.approvals["a"] = {"id": "a", "status": "pending", "summary": "private summary",
                        "arguments": {"command": "private command"}}
    for staff in ("staff", ""):
        t.staff_id = staff
        payload = CardTransport.card_data(t, project(t))
        assert not PRIVATE_FIELDS & payload["cardData"]["cardParamMap"].keys()
        if staff:
            assert payload["privateData"][staff]["cardParamMap"]["approvalTarget"] == "private command"
        else:
            assert "privateData" not in payload


@pytest.mark.asyncio
async def test_create_and_updates_keep_one_card_and_explicit_user_id_type():
    api = CardTransport("app", "placeholder", "template")
    api.request = AsyncMock(return_value={"success": True})
    t = Turn("same-card", "s", "u", "staff", "conversation")
    await api.create(t, {"status": "处理中"})
    payload = api.request.await_args.args[2]
    assert payload["outTrackId"] == "same-card"
    assert payload["openSpaceId"] == "dtv1.card//IM_ROBOT.staff"
    assert payload["callbackType"] == "STREAM" and payload["userIdType"] == 1
    assert payload["imRobotOpenSpaceModel"]["supportForward"] is False
    t.is_group = True
    await api.create(t, {})
    assert api.request.await_args.args[2]["openSpaceId"] == "dtv1.card//IM_GROUP.conversation"
    await api.stream(t, "answer", final=True)
    data = api.request.await_args.args[2]
    assert data["outTrackId"] == "same-card" and data["isFinalize"] is True
    assert data["key"] == "content" and data["isFull"] is True


@pytest.mark.asyncio
async def test_inline_process_is_delivered_only_to_initiator():
    api = CardTransport("app", "placeholder", "template")
    api.request = AsyncMock(return_value={"success": True})
    t = Turn("card", "s", "u", "staff", "group", is_group=True)
    projection = {"content": "最终回答", "processRows": '[{"body":"完整结果"}]'}
    for method in (api.create, api.update):
        await method(t, projection)
        payload = api.request.await_args.args[2]
        assert "processRows" not in payload["cardData"]["cardParamMap"]
        assert payload["privateData"]["staff"]["cardParamMap"]["processRows"] == projection["processRows"]
    assert "processRows" in projection


@pytest.mark.asyncio
async def test_stream_is_the_only_answer_writer():
    api = CardTransport("app", "placeholder", "template")
    api.request = AsyncMock(return_value={"success": True})
    t = Turn("t", "s", "u", "staff", "c")
    await api.update(t, {"content": "partial", "finalContent": "final"})
    data = api.request.await_args.args[2]["cardData"]["cardParamMap"]
    assert "content" not in data and data["finalContent"] == "final"


@pytest.mark.asyncio
async def test_top_delivery_private_scope_reopen_and_close_query():
    api = CardTransport("app", "placeholder", "main-template")
    api.request = AsyncMock(return_value={"success": True, "result": {"deliverResults": [{"success": True}]}})
    t = Turn("t", "s", "u", "staff", "conversation", top_expires=2000000000)
    await api.open_top(t, {"approvalTarget": "private command", "hasApproval": "yes"}, "top-template")
    payload = api.request.await_args.args[2]
    assert payload["outTrackId"] == "t_approval"
    assert payload["cardTemplateId"] == "top-template"
    assert payload["openSpaceId"] == "dtv1.card//ONE_BOX.conversation"
    assert payload["callbackType"] == "STREAM"
    assert payload["topOpenDeliverModel"]["userIds"] == ["staff"]
    assert payload["topOpenDeliverModel"]["expiredTimeMillis"] == 2000000000000
    assert payload["cardData"]["cardParamMap"] == {}
    assert payload["privateData"]["staff"]["cardParamMap"]["approvalTarget"] == "private command"
    t.top_created = True
    await api.open_top(t, {}, "top-template")
    assert api.request.await_args.args[1] == "/v1.0/card/instances/deliver"
    await api.close_top(t)
    api.request.assert_awaited_with("POST", "/v1.0/card/tops/close", {
        "openConversationId": "conversation", "outTrackId": "t_approval"}, query=True)


@pytest.mark.asyncio
async def test_top_nested_delivery_failure_is_not_success():
    from qpai.transport import CardAPIError
    api = CardTransport("app", "placeholder", "template")
    api.request = AsyncMock(return_value={"success": True, "result": {"deliverResults": [{"success": False}]}})
    with pytest.raises(CardAPIError, match="吊顶投放失败"):
        await api.open_top(Turn("t", "s", "u", "staff", "c"), {}, "top-template")
    with pytest.raises(CardAPIError):
        await api.open_top(Turn("t", "s", "u", "", "c"), {}, "top-template")
