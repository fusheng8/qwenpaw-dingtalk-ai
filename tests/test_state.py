import json
from qpai.state import Turn, Store, pages, identity, project, detail


def test_utf8_pages_are_lossless():
    content = "你好🌏\n```python\nprint('x')\n```" * 500
    result = pages(content, 1800)
    assert "".join(result) == content
    assert all(len(s.encode()) <= 1800 for s in result)


def test_history_and_private_details_survive_restart(tmp_path):
    t = Turn("t", "session", "user", "staff", "conversation")
    raw = "完整结果" * 10000
    t.step("call-1", "tool", "python report.py").content = raw
    db = Store(tmp_path / "turns.sqlite3"); db.save(t); db.close()
    db = Store(tmp_path / "turns.sqlite3"); restored = db.get("t")
    assert restored.steps[0].content == raw
    chunks = pages(raw, 1800)
    # Detail includes a separator after empty arguments; nothing is dropped.
    assert detail(restored, "step", step_id="call-1")["detailBody"] == pages("\n\n" + raw)[0]
    assert len(chunks) > 50
    assert raw not in json.dumps(project(restored))
    db.close()


def test_completion_collapses_history_without_losing_it():
    t = Turn("t", "s", "u", "staff", "c")
    t.step("thought", "reasoning", "思考").content = "模型提供的推理文本"
    t.step("tool", "tool", "ls").content = "file.txt"
    before = detail(t, "step", step_id="tool")
    assert before["detailEpoch"] == "active"
    assert json.loads(project(t)["toolButtons"])[0]["text"] == "ls"
    t.status, t.answer = "completed", "最终回答"
    final = project(t)
    assert final["thought"] == "" and final["toolButtons"] == "[]"
    assert final["epoch"] != before["detailEpoch"]
    assert detail(t, "step", step_id="tool")["detailEpoch"] == "done"
    assert final["content"] == "最终回答"


def test_ids_isolate_agents_and_messages():
    assert identity("a", "1") == identity("a", "1")
    assert len({identity("a", "1"), identity("a", "2"), identity("b", "1")}) == 3
