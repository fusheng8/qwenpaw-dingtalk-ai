# 千问派 · 钉钉 AI 单卡渠道

独立的 QwenPaw 渠道插件。每条用户消息对应一张 AI 卡片，在同一张卡片内呈现思考、工具调用、审批和最终回答。

> 支持 QwenPaw 2.2.1；声明兼容范围为 `>=2.2.1,<2.3.0`。卡片使用钉钉原生组件，不是嵌入网页。首次使用仍需用户扫码授权、导入发布模板并填写模板 ID。

## 一行安装

在运行千问派的机器上执行：

```sh
qwenpaw plugin install https://github.com/fusheng8/qwenpaw-dingtalk-ai/releases/download/v1.0.3/dingtalk-ai-1.0.3.zip
```

千问派已运行时，官方 CLI 会尝试热安装；未运行时，下次启动生效。安装后刷新控制台，在侧栏打开 **钉钉 AI**。升级同版本可在命令末尾加 `--force`。

## AI 卡片导入与配置

1. [下载卡片导入文件](https://github.com/fusheng8/qwenpaw-dingtalk-ai/releases/download/v1.0.3/dingtalk-ai-card.json)，也可在插件设置页点击「下载 AI 卡片模板」。
2. 在 [钉钉卡片平台](https://card.dingtalk.com/) 新建 **AI 卡片**，通过模板编辑器的 JSON 导入功能选择该文件，保存并发布。复制完整模板 ID。
3. 千问派侧栏「钉钉 AI」→「扫码填入凭据」，用钉钉完成授权。也可以手动填写已有企业内部应用的 Client ID、Client Secret。
4. 填写刚发布的卡片模板 ID，启用渠道，保存。Robot Code 通常留空即可。
5. 应用应有机器人能力、接收模式为 **Stream**，开通互动卡片实例创建、投放、更新和流式更新接口需要的权限，并发布到测试用户可访问的范围。权限申请和组织管理员审批不能由插件代替。
6. 若与官方钉钉渠道使用同一应用，勾选停用官方渠道。不要同时运行多个使用相同 Client ID 的 Stream 接收服务，否则消息或按钮回调可能被其他实例消费。

扫码复用千问派官方的 `/api/config/channels/dingtalk/qrcode` 和状态接口。扫码只自动填写应用凭据，**不会自动生成卡片模板 ID，也不代表应用权限已经获批**。

### 从旧版升级

1.0.3 将所有外部工具、服务、命令及文件操作统一为思考过程区内的灰色单行活动摘要，显示图标、中文状态和操作目标，过长省略。展开后显示浅灰圆角结果框、工具名称、代码格式的完整参数和结果、执行状态及本页复制。顶部显示处理时长，思考实时展开，点击工具命令行直接展开结果，完成后过程自动收起。保留 1.0.1 的按钮成功判定修复和中文示例。请同时升级插件并重新导入、发布新模板。可以在原模板中导入后重新发布以保留模板 ID；若新建模板，则需要在插件配置中更新 ID。

```sh
qwenpaw plugin install https://github.com/fusheng8/qwenpaw-dingtalk-ai/releases/download/v1.0.3/dingtalk-ai-1.0.3.zip --force
```

## 交互行为

- 接到任务后创建卡片；提供方实际输出的 reasoning 流实时更新。模型没有公开 reasoning 时，只显示处理状态，不编造思考内容。
- 顶部用浅灰标题显示「正在处理／已处理 N 秒」。思考默认展开，工具默认只显示命令或工具名；点击标题在原位置展开结果，不再使用过程查看按钮。
- 过程页通过钉钉私有变量投放给本轮发起者。展开和收起在客户端本地完成，不需要插件回调；长内容的上一页／下一页在对应工具的展开区内，每批 8 项过程的较早／较新导航位于过程区底部。
- 审批到来时原卡片显示「批准本次」「拒绝」。绑定当前智能体、当前会话、当前请求和发起者的钉钉 userId；重复、过期、错卡片和其他用户的点击不会执行审批。
- 完成时过程切换为独立的默认折叠状态，思考和工具也默认折叠；最终回答在分隔线下保持展开。点击「已处理 N 秒」可原位展开过程，再点击工具行展开结果。
- 长回答和工具结果受钉钉卡片大小限制，默认每页约 1800 UTF-8 字节；长回答首屏显示第一页并提供「阅读全文」。详情不会因预览长度被丢弃。
- 同一用户连续消息按原消息逐条处理，每条独立卡片。群聊按用户隔离会话，审批仅限本轮发起者。
- 重启或停用渠道后，在原卡片标记中断，旧审批失效。完整过程保存在当前智能体工作目录的 `dingtalk-ai/turns.sqlite3`，默认保留 30 天，可在界面修改为 1–365 天。

## 范围与验证边界

本插件实现钉钉原生卡片可支持的对应交互，无法做到任意 AI 网页的像素级一致。已投放的过程页可以直接展开；翻页需要连接插件服务。卡片审批是 **QwenPaw 工具执行审批**，不是钉钉 OA 工作流审批。

目前面向文本、模型 reasoning、结构化工具事件和工具审批；不额外发送附件气泡，不提供定时任务主动推送。输入图片/文件沿用官方渠道接收能力；模型输出的非文本附件尚未集成到卡片下载区。

已通过 26 项自动化测试，覆盖真实 QwenPaw 2.2.1 插件加载与流事件分发、对话生命周期、原生折叠状态、结果无损分页、过程详情私有投放、审批隔离与重复点击、重启恢复和模板结构。已在隔离的真实千问派控制台验证图形配置页面、表单校验与保存，并通过官方 CLI 的 URL ZIP 安装。**卡片平台导入发布、手机/桌面客户端渲染、组织扫码权限及真实审批闭环仍需实际钉钉账号验收；代码和结构测试不能替代该验收。**

## 开发

```sh
uv venv --python 3.11
uv pip install --python .venv/bin/python 'qwenpaw==2.2.1' pytest pytest-asyncio
.venv/bin/python -m pytest tests -q
python scripts/build_card.py
python scripts/package.py
```

`channel.py` 继承官方渠道的 Stream 接收及输入媒体处理，替换整个回复生命周期；`state.py` 持久化完整事件并生成公共/私有视图；`transport.py` 只操作同一 outTrackId；`ui/index.js` 使用千问派宿主 React/Ant Design，无额外 Node 运行依赖。

发布 ZIP 采用显式文件白名单，不包含凭据、数据库、虚拟环境或测试数据。发布页附有 `SHA256SUMS`。

## 参考

- [QwenPaw 源码](https://github.com/QwenLM/QwenPaw)
- [钉钉 AI 卡片](https://open.dingtalk.com/document/orgapp/ai-card-template)
- [卡片事件回调与 2 秒响应限制](https://open.dingtalk.com/document/orgapp/event-callback-card)
- [流式更新](https://open.dingtalk.com/document/orgapp/api-streamingupdate)
- [钉钉官方卡片示例](https://github.com/open-dingtalk/dingtalk-card-examples)

模板按公开组件协议独立生成，包含配套编辑器结构与原生 widget XML，不依赖第三方商业模板。
