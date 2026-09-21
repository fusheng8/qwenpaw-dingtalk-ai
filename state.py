"""Transport-independent, durable per-request card state.

Full tool output stays on disk; card pages are UTF-8 bounded projections.
No credentials or session webhooks are persisted here.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
STATUS_LABELS = {"running": "执行中", "completed": "已完成", "failed": "执行失败",
                 "cancelled": "已停止", "interrupted": "已中断", "pending": "待审批",
                 "approved": "已批准", "denied": "已拒绝", "expired": "已过期"}


def text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def pages(value: str, limit: int = 1800) -> list[str]:
    """Split without dropping characters or cutting UTF-8 code points."""
    if limit < 4:
        raise ValueError("page limit must be at least four bytes")
    result, part, size = [], [], 0
    for char in value:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            result.append("".join(part))
            part, size = [], 0
        part.append(char)
        size += width
    result.append("".join(part))
    return result


def identity(agent_id: str, message_id: str) -> str:
    return "qpai_" + hashlib.sha256(f"{agent_id}\0{message_id}".encode()).hexdigest()[:40]


@dataclass
class Step:
    id: str
    kind: str
    title: str
    content: str = ""
    status: str = "running"
    name: str = ""
    arguments: str = ""


@dataclass
class Turn:
    id: str
    session_id: str
    user_id: str
    staff_id: str
    conversation_id: str
    agent_id: str = "default"
    is_group: bool = False
    status: str = "running"
    answer: str = ""
    answer_id: str = ""
    steps: list[Step] = field(default_factory=list)
    approvals: dict[str, dict] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    delivered: bool = False
    finalized: bool = False
    error: str = ""
    revision: int = 0
    ended: float = 0
    view_pages: dict[str, int] = field(default_factory=dict)

    def touch(self):
        self.updated = time.time()
        self.revision += 1

    def step(self, key: str, kind: str, title: str) -> Step:
        for item in self.steps:
            if item.id == key:
                return item
        item = Step(key, kind, title)
        self.steps.append(item)
        return item

    def set_answer(self, key: str, content: str):
        if self.answer_id and self.answer_id != key and self.answer:
            self.step(self.answer_id, "progress", "执行说明").content = self.answer
        self.answer_id, self.answer = key, content
        self.touch()

    def pending(self) -> list[dict]:
        return [v for v in self.approvals.values() if v["status"] == "pending"]


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS turns (id TEXT PRIMARY KEY, session TEXT, user TEXT, updated REAL, data TEXT)")
        self.db.commit()

    def save(self, turn: Turn):
        self.db.execute("INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?)", (
            turn.id, turn.session_id, turn.user_id, turn.updated,
            json.dumps(asdict(turn), ensure_ascii=False),
        ))
        self.db.commit()

    @staticmethod
    def decode(raw: str) -> Turn:
        data = json.loads(raw)
        data["steps"] = [Step(**s) for s in data["steps"]]
        return Turn(**data)

    def get(self, key: str) -> Turn | None:
        row = self.db.execute("SELECT data FROM turns WHERE id=?", (key,)).fetchone()
        return self.decode(row[0]) if row else None

    def latest(self, session: str, user: str) -> Turn | None:
        row = self.db.execute("SELECT data FROM turns WHERE session=? AND user=? ORDER BY updated DESC LIMIT 1", (session, user)).fetchone()
        return self.decode(row[0]) if row else None

    def recent(self, limit: int = 100) -> list[Turn]:
        return [self.decode(r[0]) for r in self.db.execute("SELECT data FROM turns ORDER BY updated DESC LIMIT ?", (limit,))]

    def prune(self, days: int):
        self.db.execute("DELETE FROM turns WHERE updated < ?", (time.time() - days * 86400,))
        self.db.commit()

    def close(self):
        self.db.close()


def button(label: str, action: str, turn: Turn, **params) -> dict:
    # Loop + SingleButton, supported on both mobile and desktop clients.
    return {"text": label, "action": action, "turn_id": turn.id,
            "step_id": "", "approval_id": "", "page": "0",
            **{k: str(v) for k, v in params.items()}}


def activity(step: Step) -> tuple[str, str]:
    """Compact status + meaningful argument; never truncate stored details."""
    try:
        args = json.loads(step.arguments)
    except (ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    name = step.name.lower()
    command = next((args[k] for k in ("command", "cmd", "code") if args.get(k)), "")
    target = next((args[k] for k in ("file_path", "path", "filename", "file", "url", "query", "pattern") if args.get(k)), "")
    if command or any(k in name for k in ("shell", "exec", "terminal", "bash", "python")):
        icon, verb, subject = "command", "运行", command or step.title
    elif any(k in name for k in ("edit", "write", "patch", "replace")):
        icon, verb, subject = "edit", "编辑", target or step.title
    elif any(k in name for k in ("read", "open", "view")):
        icon, verb, subject = "file", "读取", target or step.title
    elif any(k in name for k in ("search", "find", "grep")):
        icon, verb, subject = "tool", "搜索", target or step.title
    else:
        icon, verb, subject = "tool", "调用", step.title
    prefix = "已" if step.status == "completed" else "正在"
    if step.status in {"failed", "cancelled", "interrupted"}:
        prefix = STATUS_LABELS[step.status] + " · "
    # Fold newlines for a single activity row. Complete values remain in body.
    summary = " ".join(text(subject).split())
    if len(summary) > 160:
        summary = summary[:159] + "…"
    return f"{prefix}{verb} {summary}", icon


def project(turn: Turn, *, page_bytes: int = 1800) -> dict[str, str]:
    """Inline process pages; expanding native panels needs no callback."""
    answer_pages = pages(turn.answer, page_bytes)
    thought = next((s for s in reversed(turn.steps) if s.kind == "reasoning"), None)
    status = {
        "running": "正在处理", "waiting": "等待你的审批", "completed": "已完成",
        "failed": "执行失败", "cancelled": "已停止", "interrupted": "服务已重启，本次执行已中断",
    }.get(turn.status, turn.status)
    # Titles only, with stable IDs. Results never flood the public card.
    approval = next(iter(turn.pending()), None)
    controls = []
    if len(answer_pages) > 1:
        controls.append(button(f"阅读全文 · {len(answer_pages)} 页", "answer", turn, page=0))
    process_steps = [s for s in turn.steps if s.kind != "approval"]
    groups = [process_steps[i:i + 8] for i in range(0, len(process_steps), 8)] or [[]]
    group = max(0, min(turn.view_pages.get("_process", len(groups) - 1), len(groups) - 1))
    rows = []
    for step in groups[group]:
        body = (step.arguments + "\n\n" if step.arguments else "") + step.content
        chunks = pages(body, page_bytes)
        default_page = len(chunks) - 1 if step.kind == "reasoning" and turn.status not in TERMINAL else 0
        page = max(0, min(turn.view_pages.get(step.id, default_page), len(chunks) - 1))
        navigation = []
        if page:
            navigation.append(button("上一页", "inline_page", turn, step_id=step.id, page=page - 1))
        if page + 1 < len(chunks):
            navigation.append(button("下一页", "inline_page", turn, step_id=step.id, page=page + 1))
        is_thought = step.kind in {"reasoning", "progress"}
        title, icon = (step.title, "thought") if is_thought else activity(step)
        raw = chunks[page] or "正在等待输出…"
        fence = "`" * max(3, 1 + max((len(m[0]) for m in re.finditer(r"`+", raw)), default=0))
        rows.append({"id": step.id, "kind": "thought" if is_thought else "tool",
            "title": title, "icon": icon, "body": raw,
            "toolName": step.name or ("Shell" if icon == "command" else "工具"),
            "codeBody": f"{fence}text\n{raw}\n{fence}",
            "resultStatus": STATUS_LABELS.get(step.status, "执行中"),
            "pageLabel": f"第 {page + 1}/{len(chunks)} 页" if len(chunks) > 1 else "",
            "navigation": navigation})
    process_navigation = []
    if group:
        process_navigation.append(button("较早过程", "process_page", turn, page=group - 1))
    if group + 1 < len(groups):
        process_navigation.append(button("较新过程", "process_page", turn, page=group + 1))
    elapsed = max(0, int((turn.ended or turn.updated if turn.status in TERMINAL else time.time()) - turn.created))
    process_title = f"已处理 {elapsed} 秒" if turn.status in TERMINAL else f"{status} · {elapsed} 秒"
    approval_buttons = []
    if approval:
        for label, action in [("批准本次", "approve"), ("拒绝", "deny")]:
            approval_buttons.append(button(label, action, turn, approval_id=approval["id"]))
        approval_buttons.append(button("完整审批详情", "step", turn, step_id="approval:" + approval["id"]))
    data = {
        "status": status, "phase": turn.status, "turnId": turn.id,
        "thought": pages(thought.content, page_bytes)[-1] if thought and turn.status not in TERMINAL else "",
        "processTitle": process_title, "processRows": rows, "processNavigation": process_navigation,
        "epoch": "done" if turn.status in TERMINAL else "active",
        "controls": controls,
        "approvalTitle": approval["title"] if approval else "",
        "approvalBody": pages(approval["summary"], page_bytes)[0] if approval else "",
        "approvalButtons": approval_buttons,
        "hasApproval": "yes" if approval else "no",
        "lastMessage": (turn.answer[:100] or status),
        "content": answer_pages[0] or (turn.error if turn.status in TERMINAL else ""),
    }
    return {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in data.items()}


def detail(turn: Turn, action: str, page: int = 0, step_id: str = "", page_bytes: int = 1800) -> dict[str, str]:
    """Private projection: one viewer's expansion must not affect another."""
    if action == "history":
        chunks = [turn.steps[i:i + 6] for i in range(0, len(turn.steps), 6)] or [[]]
        page = max(0, min(page, len(chunks) - 1))
        body = "\n\n".join(f"**{s.title[:200]}** · {STATUS_LABELS.get(s.status, '处理中')}" for s in chunks[page]) or "暂无执行过程"
        controls = [button(s.title[:45] or "查看详情", "step", turn, step_id=s.id, page=0) for s in chunks[page]]
        title = f"执行过程 · {page + 1}/{len(chunks)}"
    else:
        step = next((s for s in turn.steps if s.id == step_id), None)
        if action == "step" and step is None:
            raise ValueError("工具记录不存在或已过期")
        content = turn.answer if action == "answer" else (step.arguments + "\n\n" + step.content)
        chunks = pages(content, page_bytes)
        page = max(0, min(page, len(chunks) - 1))
        body = chunks[page] or "暂时没有输出"
        title = f"{('最终回答' if action == 'answer' else step.title[:80])} · {page + 1}/{len(chunks)}"
        controls = []
    if page > 0:
        controls.append(button("上一页", action, turn, page=page - 1, step_id=step_id))
    if page + 1 < len(chunks):
        controls.append(button("下一页", action, turn, page=page + 1, step_id=step_id))
    controls.append(button("收起", "close", turn))
    return {"detailTitle": title, "detailBody": body, "detailButtons": json.dumps(controls, ensure_ascii=False), "detailVisible": "yes", "detailEpoch": "done" if turn.status in TERMINAL else "active"}
