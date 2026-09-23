import json
import pytest
from qpai.state import Turn, Store, pages, identity, project, detail


@pytest.mark.parametrize("payload,expected", [
    ({"output": "success"}, "returned"),
    ({"output": '{"exit_code": 0}'}, "returned"),
    ({"output": "text", "status": "completed"}, "returned"),
    ({"output": {"exit_code": 0}}, "completed"),
    ({"output": {"exit_code": 2}}, "failed"),
    ({"output": {"isError": True}}, "failed"),
    ({"output": "text", "success": False}, "failed"),
    ({"output": "text", "status": "blocked"}, "denied"),
])
def test_tool_status_requires_execution_evidence(payload, expected):
    from qpai.state import tool_result_status
    assert tool_result_status(payload) == expected


def test_answer_tables_become_lists_without_mutating_code_or_original():
    from qpai.state import readable_answer
    raw = "结果\n\n| 命令 | 状态 |\n| --- | --- |\n| `a \\| b` | 被拒绝 |\n| `c` | 未执行 |\n\n```text\n| x | y |\n| --- | --- |\n| 1 | 2 |\n```"
    result = readable_answer(raw)
    assert "- 命令：`a \\| b`" in result and "- 状态：被拒绝" in result
    assert "```text\n| x | y |\n| --- | --- |\n| 1 | 2 |\n```" in result
    t = Turn("t", "s", "u", "staff", "c", answer=raw, status="completed")
    assert project(t)["finalContent"] == result
    assert t.answer == raw


def test_only_exact_adjacent_prose_duplicates_are_removed():
    from qpai.state import readable_answer
    assert readable_answer("已拒绝。\n\n已拒绝。\n\n说明。") == "已拒绝。\n\n说明。"
    assert readable_answer("执行成功。\n\n执行失败。") == "执行成功。\n\n执行失败。"
    code = "```text\na\n\na\n```"
    assert readable_answer(code) == code


def test_long_thought_preview_expands_without_losing_chronology():
    t = Turn("t", "s", "u", "staff", "c")
    t.step("r", "reasoning", "思考").content = "长思考" * 500
    t.step("tool", "tool", "查询").content = "结果"
    t.view_pages["r"] = 0
    row = json.loads(project(t)["processRows"])[0]
    assert len(row["thoughtText"]) == 221
    assert row["navigation"] == [] and row["hasThoughtDetail"] == "yes"
    assert row["sheetBody"] == t.steps[0].content
    assert row["sheetPosition"] == "single"
    t.view_pages["_thought:r"] = 1
    rows = json.loads(project(t)["processRows"])
    assert [r["id"] for r in rows] == ["r", "tool"]
    assert rows[0]["thoughtText"] == row["thoughtText"]
    assert rows[0]["navigation"] == []


def test_interruption_without_answer_still_has_final_error_content():
    t = Turn("t", "s", "u", "staff", "c", status="interrupted", error="服务重启，请重新发送消息。")
    assert project(t)["finalContent"] == t.error


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
    assert "仅允许本次操作" in data["approvalHint"]
    buttons = json.loads(data["approvalButtons"])
    assert [(b["text"], b["action"]) for b in buttons[:2]] == [("允许本次执行", "approve"), ("拒绝执行", "deny")]
    assert all(b["approval_id"] == "request1" and b["turn_id"] == "t" for b in buttons[:2])


def test_compact_approval_hides_and_clears_fields_after_resolution():
    t = Turn("t", "s", "u", "staff", "c")
    a = {"id": "a", "tool_name": "shell", "summary": "读取销售数据", "status": "pending",
         "arguments": {"command": "python report.py"}}
    t.approvals["a"] = a
    d = project(t)
    assert d["approvalTarget"] == "python report.py"
    assert d["hasApprovalDetail"] == "no"
    assert len(json.loads(d["approvalButtons"])) == 2
    for status, title in [("approved", "已允许本次执行"), ("denied", "已拒绝执行"),
                          ("timeout", "确认已超时"), ("expired", "确认已失效")]:
        a["status"] = status
        d = project(t)
        assert d["hasApproval"] == "no" and d["approvalTitle"] == ""
        assert d["approvalBody"] == d["approvalTarget"] == d["approvalDetail"] == ""
        assert d["hasApprovalDetail"] == "no" and d["approvalPages"] == "[]"
        assert d["approvalAllow"] == d["approvalDeny"] == "[]"
        assert d["approvalHint"] == ""


def test_long_approval_details_are_lossless_and_pending_takes_priority():
    t = Turn("t", "s", "u", "staff", "c")
    args = {"command": "长命令🌏" * 800, "cwd": "/data"}
    summary = "很长的说明🌏" * 500
    t.approvals["a"] = {"id": "a", "status": "pending", "summary": summary, "arguments": args}
    expected = "完整参数\n" + json.dumps(args, ensure_ascii=False, indent=2) + "\n\n完整说明\n" + summary
    chunks = pages(expected)
    received = []
    for index in range(len(chunks)):
        t.view_pages["approval:a"] = index
        d = project(t)
        assert d["hasApprovalDetail"] == "yes"
        received.append(d["approvalDetail"])
        assert "参数" in d["approvalDetailTitle"]
    assert "".join(received) == expected
    t.approvals["b"] = {"id": "b", "status": "denied", "summary": "后来的其他请求"}
    assert json.loads(project(t)["approvalAllow"])[0]["approval_id"] == "a"
    t.status = "failed"
    assert project(t)["approvalAllow"] == "[]"
    assert project(t)["hasApproval"] == "no"


def test_resolving_first_approval_shows_next_pending_then_hides():
    t = Turn("t", "s", "u", "staff", "c")
    for key in ("a", "b"):
        t.approvals[key] = {"id": key, "status": "pending", "summary": key}
    t.approvals["a"]["status"] = "approved"
    d = project(t)
    assert d["hasApproval"] == "yes" and d["approvalBody"] == "b"
    assert json.loads(d["approvalAllow"])[0]["approval_id"] == "b"
    t.approvals["b"]["status"] = "denied"
    assert project(t)["hasApproval"] == "no"
    assert len(t.approvals) == 2


def test_summary_only_never_claims_to_have_parameters():
    t = Turn("t", "s", "u", "staff", "c")
    t.approvals["a"] = {"id": "a", "status": "pending", "summary": "说明" * 800}
    d = project(t)
    assert d["hasApprovalTarget"] == "no"
    assert "完整说明" in d["approvalDetailTitle"] and "参数" not in d["approvalDetailTitle"]


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


def test_all_process_steps_share_one_page_and_ignore_old_page_selection():
    t = Turn("t", "s", "u", "staff", "c")
    for i in range(100):
        t.step(str(i), "tool", "命令" + str(i)).content = "结果"
    for old_page in (0, 2, 99):
        t.view_pages["_process"] = old_page
        data = project(t)
        assert [r["id"] for r in json.loads(data["processRows"])] == [str(i) for i in range(100)]
        assert data["processNavigation"] == "[]"


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
        assert activity(step)[0] == expected.replace("正在", "执行成功 · ")
    step.arguments = json.dumps({"code": "print('x')\n" * 100})
    step.content = "```\n完整结果\n```"
    rows = json.loads(project(t)["processRows"])
    assert len(rows[-1]["title"]) < 180 and "\n" not in rows[-1]["title"]
    assert rows[-1]["body"].startswith(step.arguments)
    assert rows[-1]["codeBody"].startswith("````text\n")
    assert rows[-1]["resultStatus"] == "执行成功"


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


def test_default_three_k_budget_counts_bytes_and_preserves_overflow():
    from qpai.state import PAGE_BYTES
    assert PAGE_BYTES == 3000
    exact = "中" * 1000
    assert pages(exact) == [exact]
    assert pages(exact + "🌏") == [exact, "🌏"]
    t = Turn("t", "s", "u", "staff", "c")
    t.answer = exact + "🌏"
    projected = project(t)
    assert projected["content"] == exact
    assert json.loads(projected["controls"])[0]["action"] == "answer"
    assert detail(t, "answer", page=1)["detailBody"] == "🌏"
