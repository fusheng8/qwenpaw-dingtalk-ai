import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def test_editor_and_native_trees_have_matching_ids_and_callbacks():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    native = ET.fromstring(card["widgetInfo"])
    def flatten(node):
        yield node
        for child in node.get("children", []):
            yield from flatten(child)
    nodes = list(flatten(editor["schema"]["componentsTree"][0]))
    assert len({n["id"] for n in nodes}) == len(nodes)
    assert {n["id"] for n in nodes} == {x.get("userId") for x in native.iter() if x.get("userId")}
    assert editor["extension"]["extendType"] == "AI"
    private = {v["name"] for v in editor["variableList"] if v["private"]}
    assert {"detailBody", "detailEpoch", "detailButtons"} <= private
    actions = [x.get("onTap", "") for x in native.iter() if "'actionType','0'" in x.get("onTap", "")]
    assert actions and all("turn_id" in s and "approval_id" in s for s in actions)


def test_card_build_is_reproducible():
    import runpy
    module = runpy.run_path(str(ROOT / "scripts/build_card.py"))
    actual = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    assert module["build"]() == actual


def test_all_request_buttons_have_success_conditions_and_chinese_examples():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    buttons = [n for n in walk(editor["schema"]["componentsTree"][0]) if n["componentName"] == "SingleButton"]
    assert buttons
    for button in buttons:
        conditions = button["props"]["successCondition"]["conditions"]
        assert len(conditions) == 1
        assert conditions[0]["variable"] == "actionResult"
        assert conditions[0]["op"] == "equal" and conditions[0]["value"] == "ok"
    assert next(v for v in editor["variableList"] if v["name"] == "actionResult")["private"]
    sample = editor["mockData"]["cardData"]
    assert "分析销售数据" in editor["mockData"]["cardPrivateData"]["processRows"][1]["title"]
    assert "本月销售额" in sample["content"]


def test_native_fold_state_is_local_and_reset_at_completion():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    for name in ["process", "thought"]:
        assert nodes["qpai_p2_" + name]["props"]["contentVisible"] is True
        assert nodes["qpai_p3_" + name]["props"]["contentVisible"] is False
    for phase in (2, 3):
        assert nodes[f"qpai_p{phase}_tool"]["props"]["contentVisible"] is False
    assert "查看过程" not in card["editorData"]
    native = ET.fromstring(card["widgetInfo"])
    panels = [x for x in native.iter() if x.get("userId") in {n["id"] for n in nodes.values() if n["componentName"] == "CollapsePanel"}]
    assert len(panels) == 6
    for panel in panels:
        tap = panel[0].get("onTap")
        assert "localData" in tap and "actionType" not in tap


def test_activity_details_stay_inside_process_and_use_code_panels():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    root = ET.fromstring(card["widgetInfo"])
    for phase in (2, 3):
        process = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_process")
        tool = next(x for x in process.iter() if x.get("userId") == f"qpai_p{phase}_tool")
        assert tool[0][-1].get("maxLines") == "1"
        pane = next(x for x in tool.iter() if x.get("userId") == f"qpai_p{phase}_tool_result")
        assert pane.get("cornerRadius") == "8np" and pane.get("borderWidth") == "1np"
        assert any(x.get("attributedText") == "@subdata{'codeBody'}" for x in pane.iter())
        assert any("dtCopy" in x.get("onTap", "") for x in pane.iter())
        assert any(x.get("text") == "@subdata{'resultStatus'}" for x in pane.iter())
