(function () {
  "use strict";
  const paw = window.QwenPaw;
  const { React: R, antd: A } = paw.host;
  const h = R.createElement;
  const { Card, Typography: T, Form, Input, InputNumber, Switch, Button, Space, Alert, Checkbox, Spin, Divider } = A;
  const api = async (path, init = {}) => {
    const response = await paw.host.fetch(path, init);
    let body;
    try { body = await response.json(); } catch (_) { throw new Error(`请求失败（${response.status}）`); }
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `请求失败（${response.status}）`);
    return body;
  };
  function Settings() {
    const [form] = Form.useForm();
    const agent = paw.host.useSelectedAgent ? paw.host.useSelectedAgent() : null;
    const agentId = agent && (agent.id || agent.agentId);
    const [busy, setBusy] = R.useState(true);
    const [saving, setSaving] = R.useState(false);
    const [error, setError] = R.useState("");
    const [notice, setNotice] = R.useState("");
    const [qr, setQr] = R.useState(null);
    const [qrLoading, setQrLoading] = R.useState(false);
    const [hasSecret, setHasSecret] = R.useState(false);
    const [official, setOfficial] = R.useState(false);
    const generation = R.useRef(0);
    const alive = R.useRef(true);
    R.useEffect(() => {
      alive.current = true;
      const current = ++generation.current;
      setQr(null); setBusy(true); setError(""); form.resetFields();
      api("/dingtalk-ai/config").then(data => {
        if (generation.current !== current || !alive.current) return;
        setHasSecret(data.has_secret); setOfficial(data.official_enabled);
        form.setFieldsValue({ require_mention: true, retention_days: 30, ...data, client_secret: "" });
      }).catch(e => { if (alive.current) setError(e.message); })
        .finally(() => { if (alive.current) setBusy(false); });
      return () => { alive.current = false; generation.current++; };
    }, [agentId]);
    R.useEffect(() => {
      if (!qr || !qr.token) return;
      let cancelled = false, timer;
      const current = generation.current;
      const poll = async () => {
        try {
          const data = await api("/config/channels/dingtalk/qrcode/status?token=" + encodeURIComponent(qr.token));
          if (cancelled || generation.current !== current) return;
          if (data.status === "success") {
            const credentials = data.credentials || {};
            if (!credentials.client_id || !credentials.client_secret) throw new Error("扫码未返回完整凭据，请使用手动填写。");
            form.setFieldsValue({client_id: credentials.client_id, client_secret: credentials.client_secret});
            setNotice("已填入机器人凭据。导入并发布卡片模板后，填写模板 ID 并保存。"); setQr(null); return;
          }
          if (["expired", "fail", "failed"].includes(data.status)) { setQr({expired: true}); return; }
          timer = setTimeout(poll, 2000);
        } catch (e) { if (!cancelled) { setError(e.message); setQr(null); } }
      };
      timer = setTimeout(poll, 1500);
      return () => { cancelled = true; clearTimeout(timer); };
    }, [qr]);
    async function makeQR() {
      setQrLoading(true); setError(""); setNotice("");
      const current = generation.current;
      try {
        const data = await api("/config/channels/dingtalk/qrcode");
        if (generation.current !== current || !alive.current) return;
        const raw = data.qrcode_img || "";
        if (!raw || !data.poll_token) throw new Error("官方扫码接口未返回二维码，请手动填写。");
        setQr({image: raw.startsWith("data:image/") ? raw : "data:image/png;base64," + raw, token: data.poll_token});
      } catch (e) { if (alive.current) setError(e.message); }
      finally { if (alive.current) setQrLoading(false); }
    }
    async function save(values) {
      setSaving(true); setError(""); setNotice("");
      try {
        const data = await api("/dingtalk-ai/config", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(values)});
        setHasSecret(data.has_secret); form.setFieldValue("client_secret", "");
        setNotice(values.enabled ? "配置已保存，千问派正在重新加载渠道。请在钉钉向机器人发送消息。" : "配置已保存，渠道当前保持关闭。");
      } catch (e) { setError(e.message); }
      finally { setSaving(false); }
    }
    async function download() {
      try {
        const response = await paw.host.fetch("/dingtalk-ai/template");
        if (!response.ok) throw new Error("模板下载失败");
        const url = URL.createObjectURL(await response.blob());
        const a = document.createElement("a"); a.href = url; a.download = "dingtalk-ai-card.json"; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) { setError(e.message); }
    }
    const item = (name, label, child, props = {}) => h(Form.Item, {name, label, ...props}, child);
    return h("main", {style: {maxWidth: 840, margin: "0 auto", padding: "32px 20px 64px"}},
      h(T.Title, {level: 2, style: {marginTop: 0}}, "钉钉 AI · 单卡对话"),
      h(T.Paragraph, {type: "secondary"}, "每条消息只回复一张卡片。实时查看思考与工具进度，在卡片上审批，完成后聚焦最终回答。"),
      error && h(Alert, {type: "error", showIcon: true, message: error, style: {marginBottom: 16}, role: "alert"}),
      notice && h(Alert, {type: "success", showIcon: true, message: notice, style: {marginBottom: 16}, role: "status"}),
      h(Spin, {spinning: busy},
        h(Card, {title: "1. 连接机器人", style: {marginBottom: 20}},
          h(T.Paragraph, null, "使用钉钉扫码创建并授权机器人，或填写已有应用的凭据。扫码流程由千问派官方接口提供。"),
          h(Space, {wrap: true}, h(Button, {onClick: makeQR, loading: qrLoading}, qr ? "重新生成二维码" : "扫码填入凭据"),
            h(T.Link, {href: "https://open-dev.dingtalk.com/", target: "_blank", rel: "noopener noreferrer"}, "钉钉开发者后台")),
          qr && h("div", {style: {paddingTop: 20}, role: "status"}, qr.expired ? h(Alert, {type: "warning", message: "二维码已失效，请重新生成。"}) :
            h("div", null, h("img", {src: qr.image, alt: "用钉钉扫描以授权机器人", width: 200, height: 200, style: {background: "white", padding: 8, borderRadius: 8}}),
              h(T.Paragraph, {type: "secondary"}, "等待扫码授权…")))),
        h(Form, {form, layout: "vertical", onFinish: save, initialValues: {enabled: false, require_mention: true, retention_days: 30}},
          h(Card, {title: "2. 凭据与 AI 卡片", style: {marginBottom: 20}},
            item("client_id", "Client ID / AppKey", h(Input, {autoComplete: "off", placeholder: "ding…"}), {rules: [{required: true, message: "请扫码或填写 Client ID"}]}),
            item("client_secret", "Client Secret / AppSecret", h(Input.Password, {autoComplete: "new-password", placeholder: hasSecret ? "已保存；留空保留当前密钥" : "扫码自动填入，或手动粘贴"})),
            h(Divider),
            h(T.Paragraph, null, "下载模板，在钉钉卡片平台新建 AI 卡片并导入 JSON，保存发布后复制模板 ID。"),
            h(Space, {wrap: true, style: {marginBottom: 20}}, h(Button, {onClick: download}, "下载 AI 卡片模板"),
              h(T.Link, {href: "https://card.dingtalk.com/", target: "_blank", rel: "noopener noreferrer"}, "打开卡片平台")),
            item("card_template_id", "AI 卡片模板 ID", h(Input, {placeholder: "填写已发布模板的 ID"}), {rules: [{required: true, message: "请填写已发布的卡片模板 ID"}]}),
            item("robot_code", "Robot Code（可选）", h(Input, {placeholder: "留空使用 Client ID"}))),
          h(Card, {title: "3. 启用渠道"},
            item("require_mention", "群聊中需要 @ 机器人", h(Switch), {valuePropName: "checked"}),
            item("retention_days", "完整过程记录保留天数", h(InputNumber, {min: 1, max: 365, style: {width: 140}}), {extra: "保存在当前智能体的本地工作目录，到期后卡片仍显示最终回答，过程详情不再可查。"}),
            official && item("disable_official", "", h(Checkbox, null, "使用同一个机器人时，保存并停用官方钉钉渠道"), {valuePropName: "checked"}),
            item("enabled", "启用钉钉 AI 渠道", h(Switch), {valuePropName: "checked"}),
            h(Alert, {type: "info", showIcon: true, message: "机器人需开启 Stream 模式并具备互动卡片权限。同一应用请只保留一个 Stream 接收服务。", style: {marginBottom: 20}}),
            h(Button, {type: "primary", htmlType: "submit", loading: saving, disabled: busy}, "保存配置")))));
  }
  paw.registerRoutes("dingtalk-ai", [{path: "/dingtalk-ai", label: "钉钉 AI", component: Settings, priority: 30}]);
})();
