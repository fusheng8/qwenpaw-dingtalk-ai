"""Transport-independent, durable per-request card state.

Full tool output stays on disk; card pages are UTF-8 bounded projections.
No credentials or session webhooks are persisted here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


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


def project(turn: Turn, *, page_bytes: int = 1800) -> dict[str, str]:
    """Public card data. Heavy detail is retrieved into private card variables."""
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
    controls.append(button(f"查看过程 · {len(turn.steps)} 项", "history", turn, page=0))
    tool_buttons = [button(s.title[:100], "step", turn, step_id=s.id) for s in turn.steps[-8:] if s.kind == "tool"] if turn.status not in TERMINAL else []
    approval_buttons = []
    if approval:
        for label, action in [("批准本次", "approve"), ("拒绝", "deny")]:
            approval_buttons.append(button(label, action, turn, approval_id=approval["id"]))
        approval_buttons.append(button("完整审批详情", "step", turn, step_id="approval:" + approval["id"]))
    data = {
        "status": status, "phase": turn.status, "turnId": turn.id,
        "thought": pages(thought.content, page_bytes)[-1] if thought and turn.status not in TERMINAL else "",
        "toolButtons": tool_buttons,
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
        body = "\n\n".join(f"**{s.title[:200]}** · {s.status}" for s in chunks[page]) or "暂无执行过程"
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
