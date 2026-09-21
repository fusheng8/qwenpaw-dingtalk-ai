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
    actions = [x.get("onTap", "") for x in native.iter() if x.get("onTap")]
    assert actions and all("turn_id" in s and "approval_id" in s for s in actions)


def test_card_build_is_reproducible():
    import runpy
    module = runpy.run_path(str(ROOT / "scripts/build_card.py"))
    actual = json.loads((ROOT / "cards/dingtalk-ai-card.json").read_text())
    assert module["build"]() == actual
