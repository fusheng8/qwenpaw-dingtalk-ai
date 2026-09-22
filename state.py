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
APPROVAL_FIELDS = {"approvalTitle", "approvalBody", "approvalButtons", "approvalAllow", "approvalDeny",
                   "approvalDetail", "approvalDetailTitle", "approvalPages", "approvalTarget",
                   "hasApprovalTarget", "hasApprovalDetail", "hasApproval", "approvalHint", "approvalOperation"}
PRIVATE_FIELDS = {"processRows", *APPROVAL_FIELDS}
STATUS_LABELS = {"running": "执行中", "completed": "已完成", "failed": "执行失败",
                 "cancelled": "已停止", "interrupted": "已中断", "pending": "待审批",
                 "approved": "已批准", "denied": "已拒绝", "expired": "已过期"}
STATUS_LABELS.update(waiting="等待确认", denied="已拒绝 · 未执行", timeout="确认超时 · 未执行",
                     expired="确认已失效", returned="已返回 · 未确认执行结果", unknown="未收到执行结果", completed="执行成功")


def tool_result_status(data: dict) -> str:
    """Only structured result metadata can establish success or failure."""
    output = data.get("output")
    sources = [output, data] if isinstance(output, dict) else [data]
    for source in sources:
        status = str(source.get("status", "")).lower()
        if status in {"denied", "rejected", "blocked"}:
            return "denied"
        if status in {"cancelled", "canceled"}:
            return "cancelled"
        if status in {"failed", "error"} or source.get("is_error") is True or source.get("isError") is True or source.get("success") is False:
            return "failed"
    for source in sources:
        status = str(source.get("status", "")).lower()
        for key in ("exit_code", "exitCode", "returncode"):
            code = source.get(key)
            if isinstance(code, int) and not isinstance(code, bool):
                return "completed" if code == 0 else "failed"
        if source.get("success") is True or status in {"success", "succeeded"}:
            return "completed"
    return "returned"


def readable_answer(raw: str) -> str:
    """Reflow pipe tables; deduplicate only adjacent identical prose blocks.

    Original content is retained in Turn.answer and the full-answer viewer.
    Fenced code is never interpreted as a table or deduplicated.
    """
    lines = raw.splitlines()
    result, i, fence = [], 0, ""
    def cells(line):
        return re.split(r"(?<!\\)\|", line.strip().strip("|"))
    while i < len(lines):
        line = lines[i]
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            result.append(line); i += 1; continue
        if not fence and i + 1 < len(lines) and "|" in line:
            headers = [c.strip() for c in cells(line)]
            separator = [c.strip() for c in cells(lines[i + 1])]
            if len(headers) > 1 and len(headers) == len(separator) and all(re.fullmatch(r":?-{3,}:?", c) for c in separator):
                j = i + 2
                rows = []
                while j < len(lines) and "|" in lines[j] and lines[j].strip():
                    values = [c.strip() for c in cells(lines[j])]
                    if len(values) != len(headers):
                        break
                    rows.append(values); j += 1
                if rows:
                    result.append("")
                    for number, values in enumerate(rows, 1):
                        result.append(f"**第 {number} 项**")
                        result.extend(f"- {h}：{v}" for h, v in zip(headers, values))
                        result.append("")
                    i = j; continue
        result.append(line); i += 1
    # Do not remove code or list repetitions, which can be intentional.
    blocks = re.split(r"\n\s*\n", "\n".join(result))
    deduped, fenced = [], False
    for block in blocks:
        has_fence = bool(re.search(r"(?m)^\s*(`{3,}|~{3,})", block))
        plain = not fenced and not has_fence and not re.search(r"(?m)^\s*(?:[-*+>]|\d+\.)\s", block)
        if not (plain and deduped and block.strip() == deduped[-1].strip()):
            deduped.append(block)
        if len(re.findall(r"(?m)^\s*(?:`{3,}|~{3,})", block)) % 2:
            fenced = not fenced
    return "\n\n".join(deduped).strip()


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
    answer_position: int | None = None
    message_id: str = ""
    reaction: str = ""

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
            previous = self.step(self.answer_id, "progress", "执行说明")
            previous.content = self.answer
            if self.answer_position is not None:
                self.steps.remove(previous)
                self.steps.insert(self.answer_position, previous)
        if self.answer_id != key:
            # Reserve the first-arrival position: later tool events must not
            # jump ahead when this answer becomes an intermediate explanation.
            self.answer_position = len(self.steps)
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
    prefix = "正在" if step.status == "running" else STATUS_LABELS.get(step.status, "状态未知") + " · "
    # Fold newlines for a single activity row. Complete values remain in body.
    summary = " ".join(text(subject).split())
    if len(summary) > 90:
        summary = summary[:89] + "…"
    return f"{prefix}{verb} {summary}", icon


def project(turn: Turn, *, page_bytes: int = 1800) -> dict[str, str]:
    """Inline process pages; expanding native panels needs no callback."""
    answer_pages = pages(readable_answer(turn.answer), page_bytes)
    thought = next((s for s in reversed(turn.steps) if s.kind == "reasoning"), None)
    status = {
        "running": "正在处理", "waiting": "等待你的审批", "completed": "已完成",
        "failed": "执行失败", "cancelled": "已停止", "interrupted": "服务已重启，本次执行已中断",
    }.get(turn.status, turn.status)
    # Titles only, with stable IDs. Results never flood the public card.
    approval = next(iter(turn.pending()), None) if turn.status not in TERMINAL else None
    controls = []
    if len(answer_pages) > 1:
        controls.append(button("阅读全文", "answer", turn, page=0))
    process_steps = [s for s in turn.steps if s.kind != "approval" and (s.kind not in {"reasoning", "progress"} or s.content.strip())]
    rows = []
    for step in process_steps:
        body = (step.arguments + "\n\n" if step.arguments else "") + step.content
        is_thought = step.kind in {"reasoning", "progress"}
        chunks = [body] if is_thought else pages(body, page_bytes)
        default_page = len(chunks) - 1 if step.kind == "reasoning" and turn.status not in TERMINAL else 0
        page = max(0, min(turn.view_pages.get(step.id, default_page), len(chunks) - 1))
        navigation = []
        if page:
            navigation.append(button("上一页", "inline_page", turn, step_id=step.id, page=page - 1))
        if page + 1 < len(chunks):
            navigation.append(button("下一页", "inline_page", turn, step_id=step.id, page=page + 1))
        title, icon = (step.title or "思考过程", "thought") if is_thought else activity(step)
        raw = chunks[page] or "正在等待输出…"
        thought_text = raw
        if is_thought:
            thought_text = raw[:220] + ("…" if len(raw) > 220 else "")
            navigation = []
        fence = "`" * max(3, 1 + max((len(m[0]) for m in re.finditer(r"`+", raw)), default=0))
        rows.append({"id": step.id, "kind": "thought" if is_thought else "tool",
            "title": title, "icon": icon, "body": "" if is_thought else raw,
            "thoughtText": thought_text if is_thought else "",
            "hasThoughtDetail": "yes" if is_thought and len(raw) > 220 else "no",
            "sheetTitle": ("完整思考" if step.kind == "reasoning" else "完整执行说明") if is_thought else f"{step.name or step.title or '工具详情'} · 第 {page + 1}/{len(chunks)} 页",
            "sheetBody": raw if is_thought else STATUS_LABELS.get(step.status, "执行中") + "\n\n" + raw,
            "sheetPosition": "single" if len(chunks) == 1 else "start" if page == 0 else "end" if page == len(chunks) - 1 else "middle",
            "turn_id": turn.id, "previousPage": str(max(0, page - 1)), "nextPage": str(min(len(chunks) - 1, page + 1)),
            "toolName": step.name or ("Shell" if icon == "command" else "工具"),
            "codeBody": "" if is_thought else f"{fence}text\n{raw}\n{fence}",
            "resultStatus": STATUS_LABELS.get(step.status, "执行中"),
            "pageLabel": f"第 {page + 1}/{len(chunks)} 页" if len(chunks) > 1 else "",
            "navigation": navigation})
    process_navigation = []
    elapsed = max(0, int((turn.ended or turn.updated if turn.status in TERMINAL else time.time()) - turn.created))
    process_title = f"已处理 {elapsed} 秒" if turn.status in TERMINAL else f"{status} · {elapsed} 秒"
    approval_buttons = []
    if approval and approval["status"] == "pending" and turn.status not in TERMINAL:
        for label, action in [("允许本次执行", "approve"), ("拒绝执行", "deny")]:
            approval_buttons.append(button(label, action, turn, approval_id=approval["id"]))
    approval_data = approval_view(turn, approval, page_bytes)
    data = {
        "status": status, "phase": turn.status, "turnId": turn.id,
        "thought": pages(thought.content, page_bytes)[-1] if thought and turn.status not in TERMINAL else "",
        "hasProcess": "yes" if process_steps else "no",
        "processTitle": process_title, "processRows": rows, "processNavigation": process_navigation,
        "epoch": "done" if turn.status in TERMINAL else "active",
        "controls": controls,
        **approval_data,
        "approvalButtons": approval_buttons,
        "approvalAllow": [b for b in approval_buttons if b["action"] == "approve"],
        "approvalDeny": [b for b in approval_buttons if b["action"] == "deny"],
        "hasApproval": "yes" if approval else "no",
        "lastMessage": (turn.answer[:100] or status),
        "content": answer_pages[0] or (turn.error if turn.status in TERMINAL else ""),
        "finalContent": (answer_pages[0] or turn.error) if turn.status in TERMINAL else "",
    }
    return {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in data.items()}


def approval_view(turn: Turn, approval: dict | None, page_bytes: int) -> dict:
    data = {"approvalTitle": "", "approvalBody": "", "approvalTarget": "", "approvalHint": "", "approvalOperation": "",
            "hasApprovalTarget": "no", "hasApprovalDetail": "no", "approvalDetail": "",
            "approvalDetailTitle": "展开完整说明", "approvalPages": []}
    if not approval:
        return data
    summary = text(approval.get("summary"))
    args = approval.get("arguments") or {}
    if not isinstance(args, dict):
        args = {}
    target = next((text(args[k]) for k in ("command", "cmd", "file_path", "path", "url") if args.get(k)), "")
    tool = approval.get("tool_name") or approval.get("title", "待确认操作")
    state = approval["status"]
    if state == "pending" and turn.status in TERMINAL:
        state = "expired"
    data["approvalTitle"] = {"pending": "需要你的确认", "approved": "已允许本次执行", "denied": "已拒绝执行",
        "timeout": "确认已超时", "expired": "确认已失效"}.get(state, "确认已结束")
    data["approvalHint"] = "仅允许本次操作，不会自动批准后续操作。" if state == "pending" else ""
    brief = pages(summary, min(600, page_bytes))
    data["approvalOperation"] = "操作 · " + tool
    data["approvalBody"] = brief[0] + ("…" if len(brief) > 1 else "")
    if target:
        data["hasApprovalTarget"] = "yes"
        data["approvalTarget"] = pages(target, min(480, page_bytes))[0] + ("…" if len(target.encode()) > min(480, page_bytes) else "")
    # Show short, complete arguments directly. Longer data uses a local fold
    # with bounded lossless pages; never truncate the persisted approval.
    extra = {k: v for k, v in args.items() if text(v) != target}
    needs_details = len(brief) > 1 or len(target.encode()) > min(480, page_bytes) or bool(extra)
    if needs_details:
        full = ("完整参数\n" + text(args) + "\n\n" if args else "") + "完整说明\n" + summary
        chunks = pages(full, page_bytes)
        page = max(0, min(turn.view_pages.get("approval:" + approval["id"], 0), len(chunks) - 1))
        data.update(hasApprovalDetail="yes", approvalDetail=chunks[page],
            approvalDetailTitle=("展开完整参数与说明" if args else "展开完整说明") + (f" · {page + 1}/{len(chunks)} 页" if len(chunks) > 1 else ""))
        for label, index in (("上一页", page - 1), ("下一页", page + 1)):
            if 0 <= index < len(chunks):
                data["approvalPages"].append(button(label, "approval_page", turn, approval_id=approval["id"], page=index))
    return data


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
        content = readable_answer(turn.answer) if action == "answer" else (step.arguments + "\n\n" + step.content)
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
