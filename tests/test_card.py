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
    assert actions and all("turn_id" in s for s in actions)


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
    assert all(n["componentName"] != "CollapsePanel" for n in nodes.values())
    assert "查看过程" not in card["editorData"]
    for phase in (2, 3):
        thought = nodes[f"qpai_p{phase}_thought"]
        assert thought["componentName"] == "Grid"
        assert "actionType" not in thought["props"]
        body = nodes[f"qpai_p{phase}_node_ocmubggnywbg"]
        condition = body["props"]["visible"]["condition"]["conditions"][0]
        assert condition["variable"] == f"p{phase}_process_open"
        assert condition["op"] == ("notEqual" if phase == 2 else "equal")
        assert nodes[f"qpai_p{phase}_node_ocmac5pte72"]["props"]["hasBorder"] is False
        header = nodes[f"qpai_p{phase}_node_ocmubggnywb9"]
        assert header["title"] == "展开折叠区域"
        assert header["props"]["actionType"] == "setLocalState"
    native = ET.fromstring(card["widgetInfo"])
    local_actions = [x.get("onTap") for x in native.iter() if "'localData'" in x.get("onTap", "")]
    assert len(local_actions) == 2
    assert all("actionType" not in tap for tap in local_actions)


def test_tools_are_native_links_with_action_sheet_details():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    root = ET.fromstring(card["widgetInfo"])
    for phase in (2, 3):
        process = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_node_ocmubggnywbg")
        for position in ("single", "start", "middle", "end"):
            link = next(x for x in process.iter() if x.get("userId") == f"qpai_p{phase}_tool_{position}")
            tap = link.get("onTap")
            assert tap.startswith("@dtActionSheet{") and "sheetBody" in tap
            assert ("上一页" in tap) == (position in {"middle", "end"})
            assert ("下一页" in tap) == (position in {"start", "middle"})
            assert link[0].get("maxLines") == "1"
        assert not any(x.get("attributedText") == "@subdata{'codeBody'}" for x in process.iter())


def test_loop_strings_use_editor_loop_scope_and_thought_is_direct_content():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    nodes = list(walk(editor["schema"]["componentsTree"][0]))
    for n in nodes:
        for value in n["props"].values():
            if isinstance(value, dict) and value.get("type") == "dynamicString":
                assert "[0]" not in value["content"]
    assert any(n["props"].get("text", {}).get("content") == "${loop.text}" for n in nodes)
    for phase in (2, 3):
        thought = next(n for n in nodes if n["id"] == f"qpai_p{phase}_thought")
        assert [n["componentName"] for n in thought["children"]] == ["BaseText", "Loop"]
        assert not any(n.get("props", {}).get("actionType") == "setLocalState" for n in walk(thought))


def test_disclosure_insets_and_arrow_layout_match_editor_and_native():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(n):
        yield n
        for child in n.get("children", []): yield from walk(child)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    xml = {x.get("userId"): x for x in ET.fromstring(card["widgetInfo"]).iter() if x.get("userId")}
    for phase in (2,3):
        key = f"qpai_p{phase}_node_ocmubggnywb9"
        assert nodes[key]["props"]["margin"] == -2
        assert nodes[key]["props"]["marginLeft"] == 12
        assert xml[key].get("marginLeft") == "12np"
        title, opened, closed = nodes[key]["children"]
        assert title["props"]["enableColSpan"] is False
        assert xml[title["id"]].get("width") == "match_content"
        assert closed["children"][0]["props"]["icon"]["value"]["icon"] == "icon_XDS_rightarrow"
        assert opened["children"][0]["props"]["icon"]["value"]["icon"] == "icon_XDS_downarrow"
        assert all("onTap" not in xml[n["id"]].attrib for n in (opened, closed))


def test_thinking_is_secondary_and_answer_retains_markdown():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    root = ET.fromstring(card["widgetInfo"])
    for phase in (2,3):
        thought = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_thought_body")
        assert thought.tag == "FastTextView" and thought.get("textSize") == "13np"
        assert "#70757A" in thought.get("textColor")
        answer = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_answer")
        assert answer[0].tag == "DDRichTextView"
        process = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_node_ocmubggnywbg")
        assert any(x.get("userId") == f"qpai_p{phase}_process_divider" for x in process.iter())
