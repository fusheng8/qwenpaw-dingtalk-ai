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
    async function download(top = false) {
      try {
        const response = await paw.host.fetch(top ? "/dingtalk-ai/top-template" : "/dingtalk-ai/template");
        if (!response.ok) throw new Error("模板下载失败");
        const url = URL.createObjectURL(await response.blob());
        const a = document.createElement("a"); a.href = url; a.download = top ? "dingtalk-approval-top-card.json" : "dingtalk-ai-card.json"; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) { setError(e.message); }
    }
    const item = (name, label, child, props = {}) => h(Form.Item, {name, label, ...props}, child);
    return h("main", {style: {maxWidth: 840, margin: "0 auto", padding: "32px 20px 64px"}},
      h(T.Title, {level: 2, style: {marginTop: 0}}, "钉钉 AI · 单卡对话"),
      h(T.Paragraph, {type: "secondary"}, "每条消息只回复一张卡片。实时查看思考与工具进度，在会话顶部审批，完成后聚焦最终回答。"),
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
            h(Space, {wrap: true, style: {marginBottom: 20}}, h(Button, {onClick: () => download(false)}, "下载 AI 卡片模板"),
              h(T.Link, {href: "https://card.dingtalk.com/", target: "_blank", rel: "noopener noreferrer"}, "打开卡片平台")),
            item("card_template_id", "AI 卡片模板 ID", h(Input, {placeholder: "填写已发布模板的 ID"}), {rules: [{required: true, message: "请填写已发布的卡片模板 ID"}]}),
            h(T.Paragraph, {type: "secondary"}, "审批使用另一个普通互动卡片模板，导入发布后填写下方 ID。"),
            h(Button, {onClick: () => download(true), style: {marginBottom: 16}}, "下载审批吊顶模板"),
            item("top_template_id", "审批吊顶模板 ID", h(Input, {placeholder: "导入并发布普通互动卡片后填写"}), {extra: "待审批时在会话顶部显示，处理后自动关闭。未配置时审批不会自动通过。"}),
            item("robot_code", "Robot Code（可选）", h(Input, {placeholder: "留空使用 Client ID"}))),
          h(Card, {title: "3. 启用渠道"},
            item("require_mention", "群聊中需要 @ 机器人", h(Switch), {valuePropName: "checked"}),
            item("retention_days", "完整过程记录保留天数", h(InputNumber, {min: 1, max: 365, style: {width: 140}}), {extra: "保存在当前智能体的本地工作目录，到期后卡片仍显示最终回答，过程详情不再可查。"}),
            official && item("disable_official", "", h(Checkbox, null, "使用同一个机器人时，保存并停用官方钉钉渠道"), {valuePropName: "checked"}),
            item("enabled", "启用钉钉 AI 渠道", h(Switch), {valuePropName: "checked"}),
            h(Alert, {type: "info", showIcon: true, message: "机器人需开启 Stream 模式并具备互动卡片权限。同一应用请只保留一个 Stream 接收服务。", style: {marginBottom: 20}}),
            h(Button, {type: "primary", htmlType: "submit", loading: saving, disabled: busy}, "保存配置")))));
  }
  // QwenPaw 2.2.x exposes no channel-form slot. Attach only to this plugin's
  // visible drawer and use standard input events so AntD owns saved values.
  function installDrawerQR() {
    const marker = "钉钉 AI · 单卡对话：";
    const mounts = new Map();
    let stopped = false;
    function mount(form, input, secret) {
      const box = document.createElement("section");
      box.dataset.qpaiQr = "true";
      box.setAttribute("aria-label", "钉钉扫码授权");
      box.style.cssText = "margin:16px 0 24px;";
      const title = document.createElement("div");
      title.textContent = "钉钉扫码授权";
      title.style.cssText = "font-weight:600;margin-bottom:12px;";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "获取二维码";
      button.style.cssText = "padding:7px 15px;border:1px solid #d9d9d9;border-radius:6px;background:transparent;color:inherit;cursor:pointer;font:inherit;";
      const image = document.createElement("img");
      image.alt = "请使用钉钉扫码授权机器人";
      image.width = image.height = 200;
      image.hidden = true;
      image.style.cssText = "display:none;background:white;padding:8px;margin-top:16px;max-width:100%;";
      const hint = document.createElement("p");
      hint.textContent = "与官方钉钉渠道使用相同扫码流程，授权成功后自动填入下方凭据。";
      hint.setAttribute("role", "status");
      hint.style.cssText = "font-size:13px;line-height:1.6;margin:12px 0 0;";
      box.append(title, button, image, hint);
      const field = input.closest('[class*="-form-item"]');
      (field || input).before(box);
      let generation = 0, timer, dead = false;
      const valid = id => !dead && generation === id && form.isConnected && input.isConnected && secret.isConnected
        && !!form.closest('[role="dialog"]') && form.textContent.includes(marker);
      function hideImage() { image.hidden = true; image.style.display = "none"; image.removeAttribute("src"); }
      function fill(element, value) {
        // Native setter + bubbling input is consumed by React/AntD onChange.
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        setter.call(element, value);
        element.dispatchEvent(new Event("input", {bubbles: true}));
        element.dispatchEvent(new Event("change", {bubbles: true}));
      }
      button.onclick = async () => {
        const id = ++generation;
        clearTimeout(timer); hideImage(); button.disabled = true;
        hint.textContent = "正在获取二维码…";
        try {
          const data = await api("/config/channels/dingtalk/qrcode");
          if (!valid(id)) return;
          if (!data.qrcode_img || !data.poll_token) throw new Error("官方接口未返回完整二维码信息，请重试。");
          const raw = data.qrcode_img;
          image.src = raw.startsWith("data:image/") ? raw : "data:image/png;base64," + raw;
          image.hidden = false; image.style.display = "block";
          button.textContent = "刷新二维码";
          hint.textContent = "请使用钉钉扫码，按官方页面完成机器人选择或创建及授权。";
          const deadline = Date.now() + 5 * 60 * 1000;
          let failures = 0;
          async function poll() {
            if (!valid(id)) return;
            if (Date.now() >= deadline) { hideImage(); hint.textContent = "二维码已过期，请刷新二维码。"; return; }
            try {
              const result = await api("/config/channels/dingtalk/qrcode/status?token=" + encodeURIComponent(data.poll_token));
              if (!valid(id)) return;
              failures = 0;
              if (result.status === "success") {
                const credentials = result.credentials || {};
                if (!credentials.client_id || !credentials.client_secret) {
                  hideImage(); hint.textContent = "授权未返回完整凭据，请刷新二维码重试。"; return;
                }
                fill(input, credentials.client_id); fill(secret, credentials.client_secret);
                hideImage(); hint.textContent = "扫码授权成功，已填入凭据。请填写卡片模板 ID，再点击当前窗口的保存。";
                return;
              }
              if (["expired", "fail", "failed"].includes(result.status)) {
                hideImage(); hint.textContent = result.status === "expired" ? "二维码已过期，请刷新二维码。" : "扫码授权失败，请刷新二维码重试。"; return;
              }
            } catch (_) {
              if (!valid(id)) return;
              if (++failures >= 3) { hideImage(); hint.textContent = "获取授权状态失败，请检查网络后刷新二维码。"; return; }
            }
            timer = setTimeout(poll, 5000);
          }
          timer = setTimeout(poll, 5000);
        } catch (error) { if (valid(id)) hint.textContent = error.message; }
        finally { if (valid(id)) button.disabled = false; }
      };
      return () => { dead = true; generation++; clearTimeout(timer); hideImage(); button.onclick = null; box.remove(); };
    }
    function scan() {
      if (stopped) return;
      for (const [form, cleanup] of mounts) {
        if (!form.isConnected || !form.closest('[role="dialog"]') || !form.textContent.includes(marker)) { cleanup(); mounts.delete(form); }
      }
      for (const form of document.querySelectorAll('[role="dialog"] form')) {
        if (mounts.has(form) || !form.textContent.includes(marker)) continue;
        const input = form.querySelector('input[id="client_id"], input[id$="_client_id"]');
        const secret = form.querySelector('input[id="client_secret"], input[id$="_client_secret"]');
        if (input && secret && form.querySelector('input[id="card_template_id"], input[id$="_card_template_id"]')) {
          mounts.set(form, mount(form, input, secret));
        }
      }
    }
    const observer = new MutationObserver(scan);
    observer.observe(document.body, {childList: true, subtree: true});
    scan();
    return () => { stopped = true; observer.disconnect(); for (const cleanup of mounts.values()) cleanup(); mounts.clear(); };
  }
  if (window.__qpaiDrawerCleanup) window.__qpaiDrawerCleanup();
  window.__qpaiDrawerCleanup = installDrawerQR();

  paw.registerRoutes("dingtalk-ai", [{path: "/dingtalk-ai", label: "钉钉 AI", component: Settings, priority: 30}]);
})();
