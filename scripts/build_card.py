"""Generate matching DingTalk visual-editor schema and native widget XML.

Uses documented AI containers, MarkdownBlock, Loop and SingleButton contracts.
No external build service or template compiler is required.
"""
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {"detailTitle", "detailBody", "detailButtons", "detailVisible", "detailEpoch"}
MARKDOWN = {"content", "thought", "approvalBody", "detailBody"}
LISTS = {"controls", "toolButtons", "approvalButtons", "detailButtons"}
BUTTON_KEYS = ("text", "action", "turn_id", "step_id", "approval_id", "page")
NAMES = {"status", "phase", "turnId", "epoch", "lastMessage", "flowStatus", "approvalTitle", "hasApproval", *PRIVATE, *MARKDOWN, *LISTS}
components = set()


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
    scope = "cardPrivateData" if name in PRIVATE else "cardData"
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
            rhs = data(c["valueVariable"]) if c["valueType"] == "variable" else "'" + str(c["value"]) + "'"
            terms.append("@equal{" + data(c["variable"]) + "," + rhs + "}")
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


def markdown(key, field, streaming=False):
    n = node("MarkdownBlock", key, {"content": {**ref(field), "varType": "markdown"},
        "mdVer": 0, "isStreaming": streaming, "visible": visible(), "enableLinkStatPoint": False})
    x = xml(userId=n["id"], paddingLeft="12np", paddingRight="12np", paddingTop="6np", paddingBottom="6np")
    x.append(xml("DDRichTextView", attributedText=data(field)))
    return n, x


def buttons(key, field):
    child = node("SingleButton", key + "_button", {"text": string("${" + field + "[0].text}"),
        "status": {"type": "dynamicSelect", "valueType": "fixed", "value": "normal"},
        "color": {"type": "dynamicSelect", "valueType": "fixed", "value": "gray"},
        "actionType": "request", "actionId": string("${" + field + "[0].action}"),
        "params": [{"id": str(i), "name": k, "type": "variable", "variable": field + "[0]." + k,
                    "variableType": "loop", "value": ""} for i, k in enumerate(BUTTON_KEYS[1:])],
        "visible": visible(), "marginLeft": 12, "marginRight": 12, "marginTop": 4, "marginBottom": 4,
        "successToast": string(""), "failureToast": string("操作失败，请重试"), "disabledWhileForward": True})
    n = node("Loop", key, {"listData": ref(field), "direction": "vertical", "visible": visible(),
                           "childGap": True, "childGapSize": 4, "scrollable": False}, [child])
    x = xml("ListLayout", userId=n["id"], listData=data(field), orientation="vertical")
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


def build():
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
            if phase == 2:
                append(pair, label(prefix + "status", "status"))
                append(pair, markdown(prefix + "thought", "thought"))
                append(pair, buttons(prefix + "tools", "toolButtons"))
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
        kind = "loopArray" if name in LISTS else "markdown" if name in MARKDOWN else "number" if name == "flowStatus" else "string"
        v = variable(name, kind, name in PRIVATE)
        if name in LISTS:
            v["schema"] = [variable(name + "[0]." + k, private=name in PRIVATE) for k in BUTTON_KEYS]
        variables.append(v)
    editor = {"schemaVersion": "3.0.0", "editVersion": 0, "schema": {"version": "1.0.0",
        "componentsMap": [{"package": "@ali/dxComponent", "version": "1.0.0", "exportName": name,
            "main": "./src/index.tsx", "destructuring": False, "subName": "", "componentName": name} for name in sorted(components)],
        "componentsTree": [root], "i18n": {}}, "variableList": variables,
        "mockData": {"cardData": {"flowStatus": 2, "status": "正在处理", "epoch": "active",
            "thought": "正在分析你的问题…", "content": "", "hasApproval": "no",
            "toolButtons": [{"text": "python analyse.py", "action": "step", "turn_id": "preview", "step_id": "tool1", "page": "0", "approval_id": ""}],
            "controls": []}, "cardPrivateData": {}, "localData": {}},
        "customWidgetInfo": "", "useCustomWidgetInfo": False, "formList": [], "expList": [],
        "localList": [], "hsfList": [], "lwpList": [], "extension": {"extendType": "AI", "aiStatusList": [1, 2, 3]}}
    ET.indent(native)
    return {"editorData": json.dumps(editor, ensure_ascii=False, separators=(",", ":")),
            "widgetInfo": ET.tostring(native, encoding="unicode"), "type": "im", "mode": "card"}


if __name__ == "__main__":
    target = ROOT / "cards" / "dingtalk-ai-card.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n")
    print(target)
