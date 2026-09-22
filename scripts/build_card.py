"""Generate matching DingTalk visual-editor schema and native widget XML.

Uses documented AI containers, MarkdownBlock, Loop and SingleButton contracts.
No external build service or template compiler is required.
"""
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {"detailTitle", "detailBody", "detailButtons", "detailVisible", "detailEpoch", "actionResult", "processRows"}
MARKDOWN = {"content", "thought", "approvalBody", "detailBody"}
LISTS = {"controls", "processNavigation", "approvalButtons", "detailButtons"}
BUTTON_KEYS = ("text", "action", "turn_id", "step_id", "approval_id", "page")
NAMES = {"status", "phase", "turnId", "epoch", "lastMessage", "flowStatus", "approvalTitle", "hasApproval", "processTitle", "processRows", "hasProcess", *PRIVATE, *MARKDOWN, *LISTS}
components = set()
LOCAL = {}
EXPRESSIONS = []


def variable(name, kind="string", private=False):
    return {"name": name.split(".")[-1], "id": name, "type": kind, "private": private, "editorVarType": "variables"}


def ref(name, loop=False):
    return {"type": "variableValue", "variable": name, "variableType": "loop" if loop else "global"}


def string(content):
    return {"type": "dynamicString", "content": content, "i18n": False}


def cond(name, expected="yes", other=None):
    return {"type": "variable", "variable": name, "variableType": "global", "op": "equal",
            "value": expected, "valueType": "variable" if other else "fixed",
            "valueVariable": other or "", "valueVariableType": "global"}


def visible(*conditions):
    return {"type": "dynamicVisible", "value": True, "valueType": "condition" if conditions else "fixed",
            "condition": {"op": "and", "conditions": list(conditions)}}


def node(kind, key, props=None, children=None):
    components.add(kind)
    result = {"componentName": kind, "id": "qpai_" + key, "props": props or {},
              "hidden": False, "title": "", "isLocked": False, "condition": True, "conditionGroup": ""}
    if children is not None:
        result["children"] = children
    return result


def data(name):
    scope = "localData" if name in LOCAL else "cardPrivateData" if name in PRIVATE else "cardData"
    return "@data{data." + scope + "." + name + "}"


def xml(kind="LinearLayout", **attrs):
    return ET.Element(kind, {"width": "match_parent", "height": "match_content", **attrs})


def show(expression):
    return "@triple{" + expression + ",'visible','gone'}"


def wrap(key, *conditions):
    prop = {"visible": visible(*conditions), "direction": "vertical", "marginLeft": 0,
            "marginRight": 0, "marginTop": 0, "marginBottom": 0}
    n = node("Grid", key, prop, [])
    x = xml(userId=n["id"], orientation="vertical")
    if conditions:
        terms = []
        for c in conditions:
            rhs = ("@subdata{'" + c["valueVariable"].split(".")[-1] + "'}" if c.get("valueVariableType") == "loop" else data(c["valueVariable"])) if c["valueType"] == "variable" else str(c["value"]) if isinstance(c["value"], (int, float)) else "'" + str(c["value"]) + "'"
            lhs = "@subdata{'" + c["variable"].split(".")[-1] + "'}" if c.get("variableType") == "loop" else data(c["variable"])
            term = "@equal{" + lhs + "," + rhs + "}"
            terms.append("@not{" + term + "}" if c["op"] == "notEqual" else term)
        expression = terms[0] if len(terms) == 1 else "@and{" + ",".join(terms) + "}"
        x.set("visibility", show(expression))
    return n, x


def label(key, field):
    n = node("BaseText", key, {"text": string("${" + field + "}"), "showIcon": False,
        "marginLeft": 12, "marginRight": 12, "marginTop": 6, "marginBottom": 6,
        "visible": visible(), "fontSize": 14})
    x = xml("FastTextView", userId=n["id"], text=data(field), textSize="14np", lineHeight="22np",
        marginLeft="12np", marginRight="12np", marginTop="6np", marginBottom="6np",
        textColor="@dtDarkModeAdapter{'#171A1D','#F5F5F5'}")
    return n, x


def markdown(key, field, streaming=False, loop=False):
    n = node("MarkdownBlock", key, {"content": {**ref(field, loop), "varType": "markdown"},
        "mdVer": 0, "isStreaming": streaming, "visible": visible(), "enableLinkStatPoint": False})
    x = xml(userId=n["id"], paddingLeft="12np", paddingRight="12np", paddingTop="6np", paddingBottom="6np")
    x.append(xml("DDRichTextView", attributedText="@subdata{'" + field.split('.')[-1] + "'}" if loop else data(field)))
    return n, x


def thinking_text(key):
    # MarkdownBlock does not expose a reliable text-color override in the
    # supplied editor schema. Use native text for secondary process content.
    n = node("BaseText", key, {"text": string("${loop.thoughtText}"), "visible": visible(),
        "styleType": "custom", "fontSizeType": "Custom", "customFontSize": 13,
        "customFontLineHeight": 21, "fontColorType": "Custom", "bold": False,
        "customLightColor": {"type": "dynamicColor", "valueType": "fixed", "value": "#70757A"},
        "customDarkColor": {"type": "dynamicColor", "valueType": "fixed", "value": "#AEB4BC"},
        "maxLine": {"type": "dynamicNumber", "valueType": "fixed", "value": 10000},
        "margin": -2, "marginLeft": 12, "marginRight": 12, "marginTop": 6, "marginBottom": 6})
    x = xml("FastTextView", userId=n["id"], text="@subdata{'thoughtText'}", textSize="13np", lineHeight="21np",
        maxLines="10000", marginLeft="12np", marginRight="12np", marginTop="6np", marginBottom="6np",
        textColor="@dtDarkModeAdapter{'#70757A','#AEB4BC'}")
    return n, x


def buttons(key, field, loop=False):
    child = node("SingleButton", key + "_button", {"text": string("${loop.text}"),
        "status": {"type": "dynamicSelect", "valueType": "fixed", "value": "normal"},
        "color": {"type": "dynamicSelect", "valueType": "fixed", "value": "gray"},
        "actionType": "request", "actionId": string("${loop.action}"),
        "params": [{"id": str(i), "name": k, "type": "variable", "variable": field + "[0]." + k,
                    "variableType": "loop", "value": ""} for i, k in enumerate(BUTTON_KEYS[1:])],
        "visible": visible(), "marginLeft": 12, "marginRight": 12, "marginTop": 4, "marginBottom": 4,
        "successCondition": {"op": "and", "conditions": [cond("actionResult", "ok")]},
        "successToast": string(""), "failureToast": string("操作未完成，请查看卡片中的提示"), "disabledWhileForward": True})
    n = node("Loop", key, {"listData": ref(field, loop), "direction": "vertical", "visible": visible(),
                           "childGap": True, "childGapSize": 4, "scrollable": False}, [child])
    x = xml("ListLayout", userId=n["id"], listData="@subdata{'" + field.split('.')[-1] + "'}" if loop else data(field), orientation="vertical")
    params = "@dtMapAppend{null," + ",".join("'" + k + "',@subdata{'" + k + "'}" for k in BUTTON_KEYS[1:]) + "}"
    tap = "@dtSendOutData{@dtMapAppend{null,'actionType','0','cardInstanceId',@data{data.cardInstanceId},'actionId',@subdata{'action'},'actionData',@dtMapAppend{null,'context',@dtMapAppend{@data{data.renderContext},'platform','im','platformBizId',@data{data.renderContext.mid}},'cardPrivateData',@dtMapAppend{null,'params'," + params + ",'actionIds',@dtArrayAppend{null,@subdata{'action'}}}},'requestEventId',@subdata{'action'},'requestStatusKey',@subdata{'action'}}}"
    button = xml(userId=child["id"], orientation="horizontal", childGravity="center", height="36np",
        marginLeft="12np", marginRight="12np", marginTop="4np", marginBottom="4np", cornerRadius="6np",
        borderWidth="1np", borderColor="@dtDarkModeAdapter{'#D8DADD','#54575A'}", onTap=tap)
    button.append(xml("FastTextView", text="@subdata{'text'}", textSize="14np", textGravity="center",
        maxLines="1", lineBreakMode="end", textColor="@dtDarkModeAdapter{'#007FFF','#6CB5FF'}"))
    x.append(button)
    return n, x


def append(pair, child):
    pair[0]["children"].append(child[0]); pair[1].append(child[1])


def material_disclosure(prefix, running):
    """Use the user's exported 展开折叠 material, not CollapsePanel."""
    material = json.loads((ROOT / "scripts/disclosure-material.json").read_text())
    n = material["schema"]
    x = ET.fromstring(material["native"])
    local, next_var = prefix + "process_open", prefix + "next_open"
    LOCAL[local] = "string"
    EXPRESSIONS.append({"id": next_var, "name": next_var, "type": "string", "private": False,
        "editorVarType": "expList", "expContent": f'{local} ? "" : "1"'})
    ids = {}
    def prepare(node):
        old = node["id"]; ids[old] = "qpai_" + prefix + old
        node["id"] = ids[old]; components.add(node["componentName"])
        for child in node.get("children", []): prepare(child)
    prepare(n)
    serialized = json.dumps(n, ensure_ascii=False).replace('nextExpend6', next_var).replace('expend6', local).replace('expendTitle6', 'processTitle')
    n = json.loads(serialized)
    for el in x.iter():
        if el.get("userId") in ids: el.set("userId", ids[el.get("userId")])
        for key, value in list(el.attrib.items()):
            el.set(key, value.replace('expend6', local).replace('expendTitle6', 'processTitle'))
    header, body = n["children"]
    # Drop the sample content and put real reasoning and tool links here.
    body["children"] = []
    bx = next(el for el in x.iter() if el.get("userId") == body["id"])
    for el in list(bx): bx.remove(el)
    hx = next(el for el in x.iter() if el.get("userId") == header["id"])
    action = next(c["props"] for c in header["children"] if c["props"].get("actionType") == "setLocalState")
    for key in ("actionType", "localVarAction", "stringLocalValue"):
        header["props"][key] = action[key]
    header["props"]["enableClickEvent"] = True
    hx.set("onTap", next(el.get("onTap") for el in hx.iter() if el.get("onTap")))
    # Normalize BOTH the editor schema and native XML. Material margin presets
    # otherwise override individual sides when DingTalk recompiles an import.
    native_nodes = {el.get("userId"): el for el in x.iter() if el.get("userId")}
    def spacing(node):
        props = node["props"]
        props.update(margin=-2, innerOffset=0)
        el = native_nodes[node["id"]]
        for side in ("Left", "Right", "Top", "Bottom"):
            props["margin" + side] = 0
            el.set("margin" + side, "0np")
            if node["componentName"] == "Grid":
                props["padding" + side] = {"type": "dynamicNumber", "valueType": "fixed", "value": 0}
                el.set("padding" + side, "0np")
        for child in node.get("children", []): spacing(child)
    spacing(n)
    for side, amount in (("Left", 12), ("Right", 12), ("Top", 8), ("Bottom", 4)):
        header["props"]["margin" + side] = amount
        hx.set("margin" + side, str(amount) + "np")
    title_group, expanded_arrow, collapsed_arrow = header["children"]
    title_group["props"].update(enableColSpan=False, colSpan=0, isAutoWidth=True)
    title_x = native_nodes[title_group["id"]]
    title_x.set("width", "match_content"); title_x.attrib.pop("weight", None)
    title_node = title_group["children"][0]
    title_node["props"].update(size="small", styleType="custom", fontSizeType="Custom", customFontSize=13,
        customFontLineHeight=20, bold=False,
        color={"type": "dynamicColor", "valueType": "fixed", "value": "common_level3_base_color"})
    for el in native_nodes[title_node["id"]].iter():
        if el.tag == "FastTextView":
            el.set("textSize", "13np"); el.set("lineHeight", "20np")
            el.set("textColor", "@dtDarkModeAdapter{'#74787E','#AEB4BC'}")
    for arrow, symbol in ((expanded_arrow, "icon_XDS_downarrow"), (collapsed_arrow, "icon_XDS_rightarrow")):
        arrow["props"]["marginLeft"] = 6
        # The complete header handles the click; do not fire a second toggle.
        arrow["props"].pop("actionType", None)
        arrow["props"]["enableClickEvent"] = False
        ax = native_nodes[arrow["id"]]; ax.set("marginLeft", "6np"); ax.attrib.pop("onTap", None)
        icon = arrow["children"][0]
        icon["props"]["icon"]["value"]["icon"] = symbol
        icon["props"]["color"] = {"type": "dynamicColor", "valueType": "fixed", "value": "common_level3_base_color"}
        for el in native_nodes[icon["id"]].iter():
            if el.tag == "DDIconView":
                el.set("text", "::" + symbol + "::"); el.set("textSize", "12np")
                el.set("textColor", "@dtDarkModeAdapter{'#74787E','#AEB4BC'}")
    # Separate phase keys ensure completion starts collapsed.
    def visibility(node):
        conditions = node["props"].get("visible", {}).get("condition", {}).get("conditions", [])
        for c in conditions:
            if c.get("variable") == local and running:
                if c["op"] == "equal": c["op"] = "notEqual"
                elif c["op"] == "isEmpty": c.update(op="equal", value="1")
        for child in node.get("children", []): visibility(child)
    visibility(n)
    for el in x.iter():
        v = el.get("visibility", "")
        if local in v and running:
            eq = "@equal{@toStr{" + data(local) + "},@toStr{'1'}}"
            el.set("visibility", show("@not{" + eq + "}" if "@equal" in v else eq))
    n["props"]["visible"] = visible(cond("hasProcess"))
    x.set("visibility", show("@equal{" + data("hasProcess") + ",'yes'}"))
    return (n, x), (body, bx)


def map_expr(values):
    return "@dtMapAppend{null," + ",".join("'" + key + "'," + value for key, value in values.items()) + "}"


def localized(value):
    return map_expr({"zh_CN": value, "en_US": value})


def tool_link(key, position):
    conditions = [cond("processRows[0].kind", "tool"), cond("processRows[0].sheetPosition", position)]
    for c in conditions: c["variableType"] = "loop"
    items, native_items = [], []
    actions = []
    if position in {"middle", "end"}: actions.append(("上一页", "inline_page", "previousPage"))
    if position in {"start", "middle"}: actions.append(("下一页", "inline_page", "nextPage"))
    actions.append(("关闭", "close", None))
    for index, (label, action, page) in enumerate(actions):
        values = {"action": action, "turn_id": "turn_id", "step_id": "id", "page": page}
        params, native_params = [], {}
        for name, value in values.items():
            fixed = name == "action" or value is None
            params.append({"id": name, "name": name, "type": "fixed" if fixed else "variable", "value": value or "0",
                "variable": "" if fixed else "processRows[0]." + value, "variableType": "loop"})
            native_params[name] = "'" + (value or "0") + "'" if fixed else "@subdata{'" + value + "'}"
        toast = "已切换，请再次点击工具链接查看该页" if page else ""
        items.append({"id": str(index), "actionSheetStyle": "default", "actionSheetName": string(label),
            "actionSheetDesc": string(""), "actionSheetAction": "request", "actionSheetRequestItemActionId": string(action),
            "actionSheetRequestItemParams": params, "actionSheetRequestItemSuccessToast": string(toast)})
        payload = map_expr({"actionType": "'0'", "cardInstanceId": "@data{data.cardInstanceId}", "actionId": "'" + action + "'",
            "actionData": map_expr({"context": "@data{data.renderContext}", "cardPrivateData": map_expr({"params": map_expr(native_params), "actionIds": "@dtArrayAppend{null,'" + action + "'}"})}),
            "requestEventId": "'" + action + "'", "requestStatusKey": "'" + key + "'", "successActionText": "'" + toast + "'"})
        native_items.append(map_expr({"style": "'default'", "name": localized("'" + label + "'"), "desc": localized("''"), "icon": "''", "action": "'dtSendOutData'", "data": payload}))
    n = node("Link", key, {"text": string("${loop.title}"), "maxLine": 1, "size": "small",
        "marginLeft": 12, "marginRight": 12, "marginTop": 6, "marginBottom": 6,
        "visible": visible(*conditions), "actionType": "actionSheet", "enableClickEvent": True,
        "actionSheetTitle": string("${loop.sheetTitle}"), "actionSheetMessage": string("${loop.sheetBody}"),
        "actionSheetItems": items, "disabledWhileForward": True})
    sheet = map_expr({"title": localized("@subdata{'sheetTitle'}"), "message": localized("@subdata{'sheetBody'}"),
        "items": "@dtArrayAppend{null," + ",".join(native_items) + "}"})
    x = xml(userId=n["id"], marginLeft="12np", marginRight="12np", marginTop="6np", marginBottom="6np",
        visibility=show("@and{@equal{@subdata{'kind'},'tool'},@equal{@subdata{'sheetPosition'},'" + position + "'}}"), onTap="@dtActionSheet{" + sheet + "}")
    x.append(xml("FastTextView", text="@subdata{'title'}", textSize="14np", maxLines="1", lineBreakMode="end",
        textColor="@dtDarkModeAdapter{'#007FFF','#47A9FF'}"))
    return n, x


def process_panel(prefix, running):
    outer, inside = material_disclosure(prefix, running)
    rows = node("Loop", prefix + "rows", {"listData": ref("processRows"), "direction": "vertical", "visible": visible()}, [])
    rx = xml("ListLayout", userId=rows["id"], listData=data("processRows"), orientation="vertical")
    # A Loop has one event-row prototype, keeping conditional branches inside
    # each item rather than repeating separate thought/tool prototypes.
    event = wrap(prefix + "event")
    c = cond("processRows[0].kind", "thought"); c["variableType"] = "loop"
    thought = wrap(prefix + "thought", c)
    append(thought, thinking_text(prefix + "thought_body"))
    append(thought, buttons(prefix + "thought_pages", "processRows[0].navigation", loop=True))
    append(event, thought)
    for position in ("single", "start", "middle", "end"):
        append(event, tool_link(prefix + "tool_" + position, position))
    append((rows, rx), event)
    append(inside, (rows, rx))
    append(inside, buttons(prefix + "process_pages", "processNavigation"))
    divider = node("Divider", prefix + "process_divider", {"visible": visible(), "margin": -2,
        "marginLeft": 12, "marginRight": 12, "marginTop": 10, "marginBottom": 8})
    append(inside, (divider, xml("View", userId=divider["id"], height="0.5np", marginLeft="12np", marginRight="12np",
        marginTop="10np", marginBottom="8np", backgroundColor="@dtDarkModeAdapter{'#E6E7E9','#3D4147'}")))
    return outer


def build():
    LOCAL.clear()
    EXPRESSIONS.clear()
    root = node("AICardContainer", "root", {"enablePending": True, "enableWriting": True,
        "enableFailed": False, "enableDoing": False, "enableTitle": False, "enableFlowAbort": False,
        "summaryContent": ref("lastMessage"), "flowStatusVar": ref("flowStatus"),
        "operationPenalType": "prompt", "marginLeft": 0, "marginRight": 0, "marginTop": 0, "marginBottom": 0}, [])
    native = xml(userId=root["id"], orientation="vertical", width="@data{width}", cornerRadius="8np",
        backgroundColor="@dtDarkModeAdapter{'#FFFFFF','#1E1E1E'}")
    for phase in (1, 2, 3):
        status = node("AICardStatusContainer", f"phase{phase}", {"status": phase,
            "enableExtend": False, "enableCollapse": False, "autoFoldConfig": {"needFold": False, "heightLimit": 480,
            "foldStatusLocalDataKey": "qpai_fold"}}, [])
        sx = xml(userId=status["id"], orientation="vertical", visibility=show("@equal{@toStr{" + data("flowStatus") + "},'" + str(phase) + "'}"))
        pair = (status, sx)
        prefix = f"p{phase}_"
        if phase == 1:
            append(pair, label(prefix + "status", "status"))
        else:
            content = node("AICardContent", prefix + "body", {"visible": visible()}, [])
            cx = xml(userId=content["id"], orientation="vertical")
            append(pair, (content, cx)); pair = (content, cx)
            append(pair, process_panel(prefix, phase == 2))
            append(pair, markdown(prefix + "answer", "content", streaming=phase == 2))
            approval = wrap(prefix + "approval", cond("hasApproval"))
            append(approval, label(prefix + "approval_title", "approvalTitle"))
            append(approval, markdown(prefix + "approval_body", "approvalBody"))
            append(approval, buttons(prefix + "approval_buttons", "approvalButtons"))
            append(pair, approval)
            append(pair, buttons(prefix + "controls", "controls"))
            expanded = wrap(prefix + "detail", cond("detailVisible"), cond("detailEpoch", "active" if phase == 2 else "done"))
            append(expanded, label(prefix + "detail_title", "detailTitle"))
            append(expanded, markdown(prefix + "detail_body", "detailBody"))
            append(expanded, buttons(prefix + "detail_buttons", "detailButtons"))
            append(pair, expanded)
        root["children"].append(status); native.append(sx)
    variables = []
    for name in sorted(NAMES):
        kind = "loopArray" if name in LISTS or name == "processRows" else "markdown" if name in MARKDOWN else "number" if name == "flowStatus" else "string"
        v = variable(name, kind, name in PRIVATE)
        if name in LISTS:
            v["schema"] = [variable(name + "[0]." + k, private=name in PRIVATE) for k in BUTTON_KEYS]
        if name == "processRows":
            v["schema"] = [variable("processRows[0]." + k, "markdown" if k in {"body", "codeBody"} else "string", private=True) for k in ("id", "kind", "title", "icon", "body", "thoughtText", "codeBody", "toolName", "resultStatus", "pageLabel", "sheetTitle", "sheetBody", "sheetPosition", "turn_id", "previousPage", "nextPage")]
            nav = variable("processRows[0].navigation", "loopArray", private=True)
            nav["schema"] = [variable("processRows[0].navigation[0]." + k, private=True) for k in BUTTON_KEYS]
            v["schema"].append(nav)
        variables.append(v)
    editor = {"schemaVersion": "3.0.0", "editVersion": 0, "schema": {"version": "1.0.0",
        "componentsMap": [{"package": "@ali/dxComponent", "version": "1.0.0", "exportName": name,
            "main": "./src/index.tsx", "destructuring": False, "subName": "", "componentName": name} for name in sorted(components)],
        "componentsTree": [root], "i18n": {}}, "variableList": variables,
        "mockData": {"cardData": {"flowStatus": 2, "status": "正在处理", "epoch": "active",
            "thought": "正在汇总本月销售数据，并比较各产品的销售表现。", "content": "本月销售额为 128 万元，较上月增长 12%。其中，产品甲的增长最明显。", "hasApproval": "no",
            "approvalTitle": "需要你的确认", "approvalBody": "仅允许本次操作，不会自动批准后续操作。\n\n将运行销售分析脚本，读取本地销售数据并生成汇总报告。",
            "turnId": "preview", "hasProcess": "yes", "processTitle": "正在处理 · 11 秒", "processNavigation": [],
            "processRows": [{"id": "reason1", "kind": "thought", "title": "思考过程", "body": "正在汇总本月销售数据，并比较各产品的销售表现。", "pageLabel": "", "navigation": []},
                {"id": "tool1", "kind": "tool", "icon": "command", "title": "已运行 python 分析销售数据.py", "body": "python 分析销售数据.py\n\n已读取 1,280 条销售记录，汇总报告已生成。", "pageLabel": "", "navigation": []},
                {"id": "reason2", "kind": "thought", "title": "思考过程", "body": "报告已经生成，接下来读取汇总并核对客户信息。", "pageLabel": "", "navigation": []},
                {"id": "tool2", "kind": "tool", "icon": "file", "title": "已读取 本月销售汇总.csv", "body": "本月销售汇总.csv\n\n已读取本月销售汇总。", "pageLabel": "", "navigation": []},
                {"id": "tool3", "kind": "tool", "icon": "tool", "title": "正在调用 客户信息服务", "body": "正在等待服务返回结果…", "pageLabel": "", "navigation": []}],
            "approvalButtons": [{"text": label, "action": action, "turn_id": "preview", "step_id": "", "page": "0", "approval_id": "preview-approval"} for label, action in [("允许本次执行", "approve"), ("拒绝执行", "deny")]],
            "controls": []},
        "cardPrivateData": {"actionResult": "", "detailVisible": "no", "detailEpoch": "active",
            "detailTitle": "工具执行结果", "detailBody": "已读取 1,280 条销售记录，汇总报告已生成。", "detailButtons": []}, "localData": {}},
        "customWidgetInfo": "", "useCustomWidgetInfo": False, "formList": [], "expList": EXPRESSIONS,
        "localList": [{"id": key, "name": key, "type": kind, "private": False, "editorVarType": "localList"} for key, kind in LOCAL.items()], "hsfList": [], "lwpList": [], "extension": {"extendType": "AI", "aiStatusList": [1, 2, 3]}}
    ET.indent(native)
    editor["mockData"]["cardPrivateData"]["processRows"] = editor["mockData"]["cardData"].pop("processRows")
    for row in editor["mockData"]["cardPrivateData"]["processRows"]:
        row["thoughtText"] = row["body"] if row["kind"] == "thought" else ""
        row.update({"sheetTitle": "工具详情 · 第 1/1 页", "sheetBody": row["body"], "sheetPosition": "single", "turn_id": "preview", "previousPage": "0", "nextPage": "0", "codeBody": "```text\n" + row["body"] + "\n```", "toolName": {"tool1": "Shell", "tool2": "文件读取", "tool3": "客户信息服务"}.get(row["id"], ""),
            "resultStatus": "执行中" if row["id"] == "tool3" else "已完成"})
    return {"editorData": json.dumps(editor, ensure_ascii=False, separators=(",", ":")),
            "widgetInfo": ET.tostring(native, encoding="unicode"), "type": "im", "mode": "card"}


if __name__ == "__main__":
    target = ROOT / "cards" / "dingtalk-ai-card.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(target)
