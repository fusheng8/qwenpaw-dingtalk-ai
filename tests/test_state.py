import json
from qpai.state import Turn, Store, pages, identity, project, detail


def test_interleaved_events_keep_first_arrival_order_after_restart(tmp_path):
    t = Turn("t", "s", "u", "staff", "c")
    t.step("r1", "reasoning", "思考").content = "先查询"
    t.set_answer("a1", "开始查询")
    t.set_answer("a1", "开始查询服务")
    t.step("tool1", "tool", "查询服务").content = "查询结果"
    t.step("r2", "reasoning", "思考").content = "再检查文件"
    db = Store(tmp_path / "turns.sqlite3")
    db.save(t)
    t = db.get("t")
    t.set_answer("a2", "开始检查文件")
    t.step("tool2", "tool", "读取文件").content = "文件内容"
    t.set_answer("final", "最终回答")
    t.status = "completed"
    data = project(t)
    rows = json.loads(data["processRows"])
    assert [r["id"] for r in rows] == ["r1", "a1", "tool1", "r2", "a2", "tool2"]
    assert [r["kind"] for r in rows] == ["thought", "thought", "tool", "thought", "thought", "tool"]
    assert data["content"] == "最终回答"
    db.close()


def test_confirmation_labels_keep_exact_approval_identity():
    t = Turn("t", "s", "u", "staff", "c")
    t.approvals["request1"] = {"id": "request1", "title": "执行命令", "summary": "操作详情", "status": "pending"}
    data = project(t)
    assert data["approvalTitle"] == "需要你的确认"
    assert "仅允许本次操作" in data["approvalBody"]
    buttons = json.loads(data["approvalButtons"])
    assert [(b["text"], b["action"]) for b in buttons[:2]] == [("允许本次执行", "approve"), ("拒绝执行", "deny")]
    assert all(b["approval_id"] == "request1" and b["turn_id"] == "t" for b in buttons[:2])


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
    assert json.loads(project(t)["processRows"])[1]["title"] == "正在调用 ls"
    t.status, t.answer = "completed", "最终回答"
    final = project(t)
    assert final["thought"] == ""
    assert json.loads(final["processRows"])[1]["body"] == "file.txt"
    assert not any(b["action"] == "history" for b in json.loads(final["controls"]))
    assert final["epoch"] != before["detailEpoch"]
    assert detail(t, "step", step_id="tool")["detailEpoch"] == "done"
    assert final["content"] == "最终回答"


def test_ids_isolate_agents_and_messages():
    assert identity("a", "1") == identity("a", "1")
    assert len({identity("a", "1"), identity("a", "2"), identity("b", "1")}) == 3


def test_inline_result_pages_are_lossless_and_finished_duration_stays_fixed():
    t = Turn("t", "s", "u", "staff", "c", created=100, ended=111, updated=200, status="completed")
    content = "完整工具结果🌏" * 2000
    t.step("tool", "tool", "分析销售数据").content = content
    chunks = pages(content)
    received = []
    for i in range(len(chunks)):
        t.view_pages["tool"] = i
        data = project(t)
        received.append(json.loads(data["processRows"])[0]["body"])
        assert data["processTitle"] == "已处理 11 秒"
    assert "".join(received) == content


def test_old_process_steps_remain_reachable_without_history_button():
    t = Turn("t", "s", "u", "staff", "c")
    for i in range(18):
        t.step(str(i), "tool", "命令" + str(i)).content = "结果"
    assert json.loads(project(t)["processRows"])[-1]["id"] == "17"
    t.view_pages["_process"] = 0
    first = project(t)
    assert json.loads(first["processRows"])[0]["id"] == "0"
    assert json.loads(first["processNavigation"])[0]["action"] == "process_page"


def test_all_external_activities_have_status_and_lossless_details():
    from qpai.state import activity
    t = Turn("t", "s", "u", "staff", "c")
    for name, args, expected in [
        ("exec_command", {"cmd": "python 分析.py"}, "正在运行 python 分析.py"),
        ("read_file", {"file_path": "报告.csv"}, "正在读取 报告.csv"),
        ("edit_file", {"path": "报告.csv"}, "正在编辑 报告.csv"),
        ("search", {"query": "销售数据"}, "正在搜索 销售数据"),
        ("crm_service", {}, "正在调用 crm_service"),
    ]:
        step = t.step(name, "tool", name)
        step.name, step.arguments = name, json.dumps(args, ensure_ascii=False)
        assert activity(step)[0] == expected
        step.status = "completed"
        assert activity(step)[0] == expected.replace("正在", "已")
    step.arguments = json.dumps({"code": "print('x')\n" * 100})
    step.content = "```\n完整结果\n```"
    rows = json.loads(project(t)["processRows"])
    assert len(rows[-1]["title"]) < 180 and "\n" not in rows[-1]["title"]
    assert rows[-1]["body"].startswith(step.arguments)
    assert rows[-1]["codeBody"].startswith("````text\n")
    assert rows[-1]["resultStatus"] == "已完成"


def test_empty_reasoning_does_not_create_empty_disclosure():
    t = Turn("t", "s", "u", "staff", "c", status="completed", answer="你好！")
    t.step("empty", "reasoning", "").content = "  "
    data = project(t)
    assert data["hasProcess"] == "no" and json.loads(data["processRows"]) == []
    t.steps[0].content = "有内容的思考"
    data = project(t)
    assert data["hasProcess"] == "yes"
    assert json.loads(data["processRows"])[0]["title"] == "思考过程"


def test_action_sheet_pages_preserve_full_results_and_navigation_boundaries():
    t = Turn("t", "s", "u", "staff", "c")
    step = t.step("tool", "tool", "执行命令")
    step.content = "完整输出" * 500
    total = len(pages(step.content))
    result = []
    for i in range(total):
        t.view_pages[step.id] = i
        row = json.loads(project(t)["processRows"])[0]
        result.append(row["body"])
        assert row["sheetBody"].endswith(row["body"])
        assert int(row["previousPage"]) == max(0, i - 1)
        assert int(row["nextPage"]) == min(total - 1, i + 1)
        assert row["sheetPosition"] == ("start" if i == 0 else "end" if i == total - 1 else "middle")
    assert "".join(result) == step.content
