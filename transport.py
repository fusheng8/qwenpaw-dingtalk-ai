"""DingTalk card REST transport; retries retain the same card and stream GUID."""
import asyncio
import time
import uuid

import aiohttp


class CardAPIError(RuntimeError):
    pass


class CardTransport:
    def __init__(self, client_id, client_secret, template_id, robot_code=""):
        self.client_id, self.secret = client_id, client_secret
        self.template_id, self.robot_code = template_id, robot_code or client_id
        self.http = None
        self.token, self.expires = "", 0
        self.token_lock = asyncio.Lock()

    async def start(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def close(self):
        if self.http:
            await self.http.close()

    async def access_token(self):
        async with self.token_lock:
            if self.token and time.monotonic() < self.expires:
                return self.token
            async with self.http.post("https://api.dingtalk.com/v1.0/oauth2/accessToken", json={
                "appKey": self.client_id, "appSecret": self.secret,
            }) as response:
                body = await response.json(content_type=None)
                if response.status != 200 or not body.get("accessToken"):
                    raise CardAPIError(f"获取钉钉凭据失败: HTTP {response.status}, {body.get('code', 'unknown')}")
                self.token = body["accessToken"]
                self.expires = time.monotonic() + max(1, int(body.get("expireIn", 7200)) - 120)
                return self.token

    async def request(self, method, path, data):
        for attempt in range(4):
            try:
                token = await self.access_token()
                async with self.http.request(method, "https://api.dingtalk.com" + path,
                        headers={"x-acs-dingtalk-access-token": token}, json=data) as response:
                    body = await response.json(content_type=None)
                    if response.status < 300 and body.get("success") is not False:
                        return body
                    if response.status == 401:
                        self.expires = 0
                    elif response.status != 429 and response.status < 500:
                        raise CardAPIError(f"卡片请求失败: HTTP {response.status}, {body.get('code', 'unknown')}")
                    if attempt == 3:
                        raise CardAPIError(f"卡片请求重试耗尽: HTTP {response.status}")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 3:
                    raise CardAPIError("连接钉钉卡片服务超时") from None
            await asyncio.sleep(min(0.5 * 2**attempt, 4))

    @staticmethod
    def card_data(turn, data):
        public = dict(data)
        rows = public.pop("processRows", "[]")
        result = {"cardData": {"cardParamMap": public}}
        if turn.staff_id:
            result["privateData"] = {turn.staff_id: {"cardParamMap": {"processRows": rows}}}
        return result

    async def create(self, turn, data):
        payload = {"cardTemplateId": self.template_id, "outTrackId": turn.id,
            "callbackType": "STREAM", "userIdType": 1,
            **self.card_data(turn, data),
            "imGroupOpenSpaceModel": {"supportForward": False},
            "imRobotOpenSpaceModel": {"supportForward": False}}
        if turn.is_group:
            payload.update(openSpaceId=f"dtv1.card//IM_GROUP.{turn.conversation_id}",
                imGroupOpenDeliverModel={"robotCode": self.robot_code})
        else:
            if not turn.staff_id:
                raise CardAPIError("单聊消息缺少 senderStaffId，无法投放 AI 卡片")
            payload.update(openSpaceId=f"dtv1.card//IM_ROBOT.{turn.staff_id}",
                imRobotOpenDeliverModel={"spaceType": "IM_ROBOT"})
        return await self.request("POST", "/v1.0/card/instances/createAndDeliver", payload)

    async def update(self, turn, data):
        return await self.request("PUT", "/v1.0/card/instances", {
            "outTrackId": turn.id, "userIdType": 1, **self.card_data(turn, data),
            "cardUpdateOptions": {"updateCardDataByKey": True, "updatePrivateDataByKey": True}})

    async def stream(self, turn, content, final=False):
        return await self.request("PUT", "/v1.0/card/streaming", {
            "outTrackId": turn.id, "guid": uuid.uuid4().hex, "key": "content",
            "content": content or "正在处理…", "isFull": True, "isFinalize": final,
            # Keep errors in the completed layout so history remains accessible.
            "isError": False})
