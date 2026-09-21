from .channel import DingTalkAIChannel
from .routes import router


class DingTalkAIPlugin:
    def register(self, api):
        api.register_channel(DingTalkAIChannel, label="钉钉 AI · 单卡对话",
            description="钉钉 AI · 单卡对话：支持官方扫码授权、流式思考与工具审批。",
            doc_url="https://github.com/fusheng8/qwenpaw-dingtalk-ai#readme",
            config_fields=[
                {"name": "client_id", "label": "Client ID / AppKey", "type": "text", "required": True},
                {"name": "client_secret", "label": "Client Secret / AppSecret", "type": "password", "required": True},
                {"name": "card_template_id", "label": "已发布的 AI 卡片模板 ID", "type": "text", "required": True},
                {"name": "robot_code", "label": "Robot Code（留空使用 Client ID）", "type": "text"},
                {"name": "require_mention", "label": "群聊需要 @ 机器人", "type": "switch", "default": True},
                {"name": "retention_days", "label": "过程记录保留天数", "type": "number", "default": 30},
            ])
        api.register_http_router(router, prefix="/dingtalk-ai", tags=["DingTalk AI"])


plugin = DingTalkAIPlugin()
