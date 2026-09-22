import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def test_finished_answer_uses_independent_static_binding_and_tools_are_muted():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(n):
        yield n
        for c in n.get("children", []): yield from walk(c)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    assert card["type"] == "im"
    assert nodes["qpai_p2_answer"]["props"]["content"]["variable"] == "content"
    assert nodes["qpai_p3_answer"]["props"]["content"]["variable"] == "finalContent"
    assert nodes["qpai_p3_answer"]["props"]["isStreaming"] is False
    for phase in (2, 3):
        label = nodes[f"qpai_p{phase}_tool_single_label"]
        assert label["props"]["customLightColor"]["value"] == "#70757A"
        assert label["props"]["maxLine"]["value"] == 1
        row = nodes[f"qpai_p{phase}_tool_single"]
        assert row["props"]["actionType"] == "actionSheet"


def test_approval_is_in_separate_normal_top_card():
    from qpai.state import APPROVAL_FIELDS
    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    main = json.loads(json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())["editorData"])
    main_nodes = list(walk(main["schema"]["componentsTree"][0]))
    assert not any(n["id"].endswith("approval_allow_button") for n in main_nodes)
    card = json.loads((ROOT / "cards/dingtalk-approval-top-card.json").read_text())
    editor = json.loads(card["editorData"])
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    native = ET.fromstring(card["widgetInfo"])
    assert set(nodes) == {x.get("userId") for x in native.iter() if x.get("userId")}
    # Official builder CardType.Onebox is "onebox"; import checks the envelope.
    assert card["type"] == "onebox" and card["mode"] == "card"
    assert editor["extension"]["extendType"] == "NORMAL"
    assert editor["schema"]["componentsTree"][0]["componentName"] == "Card"
    variables = {v["name"]: v for v in editor["variableList"]}
    assert all(variables[k]["private"] for k in APPROVAL_FIELDS)
    assert nodes["qpai_top_approval_allow_button"]["props"]["color"]["value"] == "blue"
    assert nodes["qpai_top_approval_deny_button"]["props"]["color"]["value"] == "gray"
    assert editor["mockData"]["cardPrivateData"]["hasApproval"] == "yes"
    for n in nodes.values():
        if n["componentName"] == "SingleButton":
            assert n["props"]["successCondition"]["conditions"][0]["variable"] == "actionResult"
    import runpy
    assert runpy.run_path(str(ROOT / "scripts/build_card.py"))["build"](top=True) == card


def test_process_loop_has_one_interleaved_event_prototype():
    card = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(node):
        yield node
        for child in node.get("children", []):
            yield from walk(child)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    native = ET.fromstring(card["widgetInfo"])
    for phase in (2, 3):
        loop = nodes[f"qpai_p{phase}_rows"]
        assert len(loop["children"]) == 1
        event = loop["children"][0]
        assert event["id"] == f"qpai_p{phase}_event"
        assert event["children"][0]["id"] == f"qpai_p{phase}_thought"
        assert len(event["children"]) == 5
        native_loop = next(x for x in native.iter() if x.get("userId") == loop["id"])
        assert len(native_loop) == 1
        assert native_loop[0].get("userId") == event["id"]


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
        assert [n["componentName"] for n in thought["children"]] == ["BaseText", "Grid"]
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
        assert thought.tag == "FastTextView" and thought.get("textSize") == "12np"
        assert "#70757A" in thought.get("textColor")
        answer = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_answer")
        assert answer[0].tag == "DDRichTextView"
        process = next(x for x in root.iter() if x.get("userId") == f"qpai_p{phase}_node_ocmubggnywbg")
        assert any(x.get("userId") == f"qpai_p{phase}_process_divider" for x in process.iter())


def test_thought_text_binds_a_string_not_a_markdown_variable():
    from qpai.state import Turn, project, pages
    card = json.loads((ROOT / 'cards/dingtalk-ai-card.json').read_text())
    editor = json.loads(card['editorData'])
    rows = next(v for v in editor['variableList'] if v['name'] == 'processRows')
    field = next(v for v in rows['schema'] if v['name'] == 'thoughtText')
    assert field['type'] == 'string' and field['private'] is True
    native = ET.fromstring(card['widgetInfo'])
    for phase in (2, 3):
        view = next(x for x in native.iter() if x.get('userId') == f'qpai_p{phase}_thought_body')
        assert view.get('text') == "@subdata{'thoughtText'}"
    turn = Turn('t', 's', 'u', 'staff', 'c')
    original = '思考内容\n**格式标记**\n' * 300
    turn.step('r', 'reasoning', '思考过程').content = original
    turn.view_pages['_thought:r'] = 1
    row = json.loads(project(turn)['processRows'])[0]
    assert row['sheetBody'] == original
    assert len(row['thoughtText']) == 221
    for phase in (2, 3):
        sheet = next(x for x in native.iter() if x.get('userId') == f'qpai_p{phase}_thought_full')
        assert sheet.get('onTap').startswith('@dtActionSheet{')
        assert 'sheetBody' in sheet.get('onTap')
        assert '上一页' not in sheet.get('onTap') and '下一页' not in sheet.get('onTap')
    turn.status = 'completed'
    assert json.loads(project(turn)['processRows'])[0]['thoughtText']


def test_ceiling_has_bounded_header_and_parallel_actions_before_details():
    card = json.loads((ROOT / "cards/dingtalk-approval-top-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(n):
        yield n
        for c in n.get("children", []): yield from walk(c)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    approval = nodes["qpai_top_approval"]
    assert [n["id"] for n in approval["children"]] == [
        "qpai_top_approval_title", "qpai_top_approval_target",
        "qpai_top_approval_actions", "qpai_top_approval_footer"]
    for key in ("title", "target_text"):
        assert nodes["qpai_top_approval_" + key]["props"]["maxLine"]["value"] == 1
    actions = nodes["qpai_top_approval_actions"]
    assert actions["props"]["direction"] == "horizontal" and len(actions["children"]) == 2
    sheet = nodes["qpai_top_approval_details"]
    assert sheet["props"]["actionType"] == "actionSheet"
    assert sheet["props"]["actionSheetMessage"]["content"] == "${approvalDetail}"
    native = ET.fromstring(card["widgetInfo"])
    elements = {x.get("userId"): x for x in native.iter() if x.get("userId")}
    assert elements[actions["id"]].get("orientation") == "horizontal"
    for action in ("allow", "deny"):
        assert elements[f"qpai_top_approval_{action}_cell"].get("width") == "120np"
        cell = nodes[f"qpai_top_approval_{action}_cell"]
        assert cell["props"]["isFixedWidth"] is True
        assert cell["children"][0]["componentName"] == "SingleButton"
        assert elements[f"qpai_top_approval_{action}_button"].get("height") == "40np"
    assert not any("cardPrivateData.approvalBody" in x.get("text", "") or
                   "cardPrivateData.approvalDetail}" in x.get("text", "") for x in native.iter())


def test_top_copy_uses_full_private_values_and_more_actions_are_bound_to_request():
    card = json.loads((ROOT / "cards/dingtalk-approval-top-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(n):
        yield n
        for c in n.get("children", []): yield from walk(c)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    native = {x.get("userId"): x for x in ET.fromstring(card["widgetInfo"]).iter() if x.get("userId")}
    for key, field in [("qpai_top_approval_target", "approvalCommand"), ("qpai_top_copy_details", "approvalDetail")]:
        assert nodes[key]["props"]["actionType"] == "copy"
        assert nodes[key]["props"]["copyValue"]["content"] == "${" + field + "}"
        assert native[key].get("onTap") == "@dtCopy{@data{data.cardPrivateData." + field + "}}"
    items = nodes["qpai_top_approval_details"]["props"]["actionSheetItems"]
    assert {i["id"] for i in items} == {"approve", "approve_similar", "deny", "cancel_approval", "close"}
    for item in items:
        params = {p["name"]: p for p in item["actionSheetRequestItemParams"]}
        assert params["approval_id"]["variable"] == "approvalId"
        assert params["turn_id"]["variable"] == "turnId"


def test_top_footer_has_explicit_spacing_and_auto_height():
    card = json.loads((ROOT / "cards/dingtalk-approval-top-card.json").read_text())
    editor = json.loads(card["editorData"])
    def walk(n):
        yield n
        for c in n.get("children", []): yield from walk(c)
    nodes = {n["id"]: n for n in walk(editor["schema"]["componentsTree"][0])}
    footer = nodes["qpai_top_approval_footer"]
    assert footer["props"]["marginTop"] == 8
    assert footer["props"]["marginBottom"] == 10
    assert all(n["props"].get("isAutoHeight") is True for n in walk(nodes["qpai_top_approval"]) if n["componentName"] == "Grid")
    native = {x.get("userId"): x for x in ET.fromstring(card["widgetInfo"]).iter() if x.get("userId")}
    assert native[footer["id"]].get("marginBottom") == "10np"
