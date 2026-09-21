"""Authenticated host routes. Secrets are write-only to this settings UI."""
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import FileResponse

router = APIRouter()
FIELDS = {"enabled", "client_id", "client_secret", "card_template_id", "robot_code",
          "require_mention", "retention_days", "page_bytes", "dm_policy", "group_policy", "allow_from"}


def as_dict(config):
    if hasattr(config, "model_dump"):
        return config.model_dump()
    return dict(config or {})


def masked(config):
    result = {k: v for k, v in config.items() if k in FIELDS and k != "client_secret"}
    result["has_secret"] = bool(config.get("client_secret"))
    return result


@router.get("/config")
async def get_config(request: Request):
    from qwenpaw.app.agent_context import get_agent_for_request
    agent = await get_agent_for_request(request)
    channels = agent.config.channels
    config = as_dict(getattr(channels, "dingtalk_ai", None))
    result = masked(config)
    official = as_dict(getattr(channels, "dingtalk", None))
    result["official_enabled"] = bool(official.get("enabled"))
    result["official_client_id"] = official.get("client_id", "")
    return result


@router.put("/config")
async def put_config(request: Request, body: dict = Body(...)):
    from qwenpaw.app.agent_context import get_agent_for_request
    from qwenpaw.app.routers.config import put_channel
    from qwenpaw.config.config import save_agent_config
    agent = await get_agent_for_request(request)
    old = as_dict(getattr(agent.config.channels, "dingtalk_ai", None))
    config = {**old, **{k: v for k, v in body.items() if k in FIELDS}}
    if not body.get("client_secret"):
        # Never accidentally carry an old bot's secret to a new Client ID.
        config["client_secret"] = old.get("client_secret", "") if config.get("client_id") == old.get("client_id") else ""
    for key in ("client_id", "client_secret", "card_template_id", "robot_code"):
        config[key] = str(config.get(key) or "").strip()
    if config.get("enabled") and not all(config.get(k) for k in ("client_id", "client_secret", "card_template_id")):
        raise HTTPException(422, "启用前请填写 Client ID、Client Secret 和已发布的卡片模板 ID")
    try:
        config["retention_days"] = int(config.get("retention_days", 30))
        if not 1 <= config["retention_days"] <= 365:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "保留天数应为 1–365") from None
    official = as_dict(getattr(agent.config.channels, "dingtalk", None))
    if config.get("enabled") and official.get("enabled") and official.get("client_id") == config["client_id"]:
        if not body.get("disable_official"):
            raise HTTPException(409, "同一个机器人正在被官方渠道使用，请勾选停用官方渠道后再保存")
        official["enabled"] = False
        from qwenpaw.config.config import DingTalkConfig
        agent.config.channels.dingtalk = DingTalkConfig(**official)
        save_agent_config(agent.agent_id, agent.config)
    result = await put_channel(request=request, channel_name="dingtalk_ai", single_channel_config=config)
    return masked(as_dict(result))


@router.get("/template")
async def template():
    return FileResponse(Path(__file__).parent / "cards" / "dingtalk-ai-card.json",
                        media_type="application/json", filename="dingtalk-ai-card.json")
