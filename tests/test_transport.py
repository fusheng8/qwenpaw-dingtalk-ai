from unittest.mock import AsyncMock
import pytest
from qpai.transport import CardTransport
from qpai.state import Turn


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
