"""验收前变更（reopen）：changes/change-vN.md、级联失效、上游变更透传。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .parsers.approval_parser import parse_approval
from .phase_gate import PHASE_CONFIGS
from .role_output import (
    ROLE_SCOPED_OUTPUTS,
    find_output,
    list_role_outputs,
    stage_dir,
)

logger = logging.getLogger(__name__)

BASE_REQUIREMENTS = "01-requirements.md"
CHANGE_FILE_RE = re.compile(r"^change-(v\d+)\.md$", re.IGNORECASE)
# 兼容旧版：requirements/01-requirements-vN.md
LEGACY_CHANGE_REQUIREMENTS_RE = re.compile(
    r"^01-requirements-(v\d+)\.md$", re.IGNORECASE
)

REOPEN_INJECT_PROMPT = "prompts/reopen_inject.md"
REVIEWED_CHANGES_KEY = "reviewed_changes"

# 节点顺序（intent 单独处理）
REOPEN_CASCADE = (
    "analyze_requirements",
    "plan_session",
    "design_test_cases",
    "split_tasks",
    "implement_code",
    "verify_tests",
    "ai_review",
    "commit_code",
)

# 图上可直接 resume 的节点（design_test_cases 挂在 plan_session 内，不是独立图节点）
REOPEN_GRAPH_NODES = (
    "analyze_requirements",
    "identify_intent",
    "plan_session",
    "split_tasks",
    "implement_code",
    "verify_tests",
    "ai_review",
    "commit_code",
)

# 路由用：某节点的「上一节点」（用于注入上游 reopen_impact）
UPSTREAM_NODE: dict[str, str] = {
    "plan_session": "analyze_requirements",
    "design_test_cases": "plan_session",
    "split_tasks": "plan_session",
    "implement_code": "split_tasks",
    "verify_tests": "implement_code",
    "ai_review": "verify_tests",
    "commit_code": "ai_review",
}


@dataclass
class ChangeDetail:
    id: str = ""
    summary: str = ""
    locate_hint: str = ""
    anchor: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "summary": self.summary,
            "locate_hint": self.locate_hint,
            "anchor": self.anchor,
        }


@dataclass
class ChangeProgress:
    """change 文件上的执行进度（引擎维护）。"""

    # pending=未跑 / in_progress=进行中 / completed=已有文档都过完
    status: str = "pending"
    last_node: str = ""
    all_docs_updated: bool = False
    updated_at: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "last_node": self.last_node,
            "all_docs_updated": self.all_docs_updated,
            "updated_at": self.updated_at,
            "note": self.note,
        }


@dataclass
class ReopenTarget:
    """本轮 change 文件。"""

    path: Path
    version: str
    logical_name: str
    run_mode: str
    primary_layer: str = ""
    change_details: list[ChangeDetail] = field(default_factory=list)
    change_scope: str = ""
    requirement_summary: str = ""
    requirement_type: str = ""
    progress: ChangeProgress = field(default_factory=ChangeProgress)
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def change_details_text(self) -> str:
        if not self.change_details:
            return "（change 文件未写 change_details）"
        lines: list[str] = []
        for item in self.change_details:
            summary = item.summary or "（无摘要）"
            if item.id and not item.id.startswith("C"):
                lines.append(f"- **{item.id}**: {summary}")
            elif item.id and (item.locate_hint or item.anchor):
                lines.append(f"- **{item.id}**: {summary}")
                if item.locate_hint:
                    lines.append(f"  - 建议落在哪类文档: `{item.locate_hint}`")
                if item.anchor:
                    lines.append(f"  - 旧文档位置: `{item.anchor}`")
            else:
                lines.append(f"- {summary}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "path": str(self.path),
            "logical_name": self.logical_name,
            "primary_layer": self.primary_layer,
            "change_scope": self.change_scope,
            "change_details": [d.to_dict() for d in self.change_details],
            "progress": self.progress.to_dict(),
        }


def changes_dir(docs_dir: Path) -> Path:
    return docs_dir / "changes"


def requirements_dir(docs_dir: Path) -> Path:
    return stage_dir(docs_dir, BASE_REQUIREMENTS)


def baseline_requirements_path(docs_dir: Path) -> Path | None:
    path = requirements_dir(docs_dir) / BASE_REQUIREMENTS
    return path if path.is_file() else None


def list_change_files(docs_dir: Path) -> list[tuple[str, Path]]:
    """返回按版本排序的 change 文件 [(v1, path), ...]。优先 changes/。"""
    folder = changes_dir(docs_dir)
    found: list[tuple[int, str, Path]] = []
    if folder.is_dir():
        for path in folder.iterdir():
            if not path.is_file():
                continue
            match = CHANGE_FILE_RE.match(path.name)
            if not match:
                continue
            version = match.group(1).lower()
            found.append((int(version[1:]), version, path))
    if found:
        found.sort(key=lambda item: item[0])
        return [(version, path) for _n, version, path in found]

    # 兼容旧路径 requirements/01-requirements-vN.md
    req = requirements_dir(docs_dir)
    if req.is_dir():
        for path in req.iterdir():
            if not path.is_file():
                continue
            match = LEGACY_CHANGE_REQUIREMENTS_RE.match(path.name)
            if not match:
                continue
            version = match.group(1).lower()
            found.append((int(version[1:]), version, path))
    found.sort(key=lambda item: item[0])
    return [(version, path) for _n, version, path in found]


def next_change_version(docs_dir: Path) -> str:
    versions = list_change_files(docs_dir)
    if not versions:
        return "v1"
    return f"v{int(versions[-1][0][1:]) + 1}"


def change_file_name(version: str) -> str:
    ver = version if version.startswith("v") else f"v{version}"
    return f"change-{ver}.md"


def change_doc_ref(version_or_name: str) -> str:
    """节点 metadata 里记录的 change 引用，如 ``changes/change-v1.md``。"""
    name = version_or_name.strip()
    if name.startswith("changes/"):
        return name
    if CHANGE_FILE_RE.match(name):
        return f"changes/{name}"
    if name.startswith("v") and name[1:].isdigit():
        return f"changes/{change_file_name(name)}"
    if name.isdigit():
        return f"changes/{change_file_name(f'v{name}')}"
    return f"changes/{change_file_name(name)}" if not name.endswith(".md") else name


def change_temp_subdir(state: dict[str, Any] | None = None, *, version: str = "") -> str:
    """本轮 change 会话的临时提示词子目录名，如 ``change-v1``。

    reopen 注入节点与切到常规交付后的 initial-tail 节点都沿用同一子目录，
    避免覆盖首轮 ``temp-prompts/<node>.prompt.md``。无 change 上下文时返回空。
    """
    state = state or {}
    ver = (version or str(state.get("change_version") or "")).strip()
    if not ver:
        change_file = str((state.get("node_outputs") or {}).get("change_file") or "")
        name = Path(change_file).name if change_file else ""
        match = CHANGE_FILE_RE.match(name)
        if match:
            ver = match.group(1)
    if not ver:
        return ""
    if not ver.startswith("v"):
        ver = f"v{ver}"
    # 目录名与 change 文件主名一致：change-v1
    stem = change_file_name(ver)
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem


def _extract_metadata_raw(content: str) -> dict[str, Any]:
    for tag in ("change_metadata", "requirement_metadata"):
        match = re.search(
            rf"```yaml\s+{tag}\s*\n(.*?)```",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if not match:
            continue
        try:
            data = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and isinstance(data.get(tag), dict):
            data = data[tag]
        if isinstance(data, dict):
            return data
    return {}


def _parse_change_details(raw: Any) -> list[ChangeDetail]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [ChangeDetail(summary=text)] if text else []
    if not isinstance(raw, list):
        return []
    details: list[ChangeDetail] = []
    for item in raw:
        if isinstance(item, str):
            details.append(ChangeDetail(summary=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        details.append(
            ChangeDetail(
                id=str(item.get("id") or "").strip(),
                summary=str(
                    item.get("summary") or item.get("detail") or item.get("desc") or ""
                ).strip(),
                locate_hint=str(
                    item.get("locate_hint")
                    or item.get("impact_hint")
                    or item.get("locate")
                    or ""
                ).strip(),
                anchor=str(item.get("anchor") or item.get("location") or "").strip(),
            )
        )
    return details


def _parse_change_progress(content: str, metadata: dict[str, Any]) -> ChangeProgress:
    match = re.search(
        r"```yaml\s+change_status\s*\n(.*?)```",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    raw: dict[str, Any] = {}
    if match:
        try:
            loaded = yaml.safe_load(match.group(1))
            if isinstance(loaded, dict):
                raw = loaded
        except yaml.YAMLError:
            raw = {}
    if not raw and isinstance(metadata.get("change_status"), dict):
        raw = metadata["change_status"]
    status = str(raw.get("status") or "pending").strip().lower() or "pending"
    if status not in {"pending", "in_progress", "completed"}:
        status = "pending"
    return ChangeProgress(
        status=status,
        last_node=str(raw.get("last_node") or "").strip(),
        all_docs_updated=bool(raw.get("all_docs_updated", False)),
        updated_at=str(raw.get("updated_at") or "").strip(),
        note=str(raw.get("note") or "").strip(),
    )


def parse_reopen_target(path: Path) -> ReopenTarget | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = CHANGE_FILE_RE.match(path.name) or LEGACY_CHANGE_REQUIREMENTS_RE.match(
        path.name
    )
    if not match:
        return None
    version = match.group(1).lower()
    raw = _extract_metadata_raw(content)
    run_mode = str(raw.get("run_mode") or "").strip().lower()
    return ReopenTarget(
        path=path,
        version=str(raw.get("change_version") or version).strip().lower() or version,
        logical_name=path.name,
        run_mode=run_mode,
        primary_layer=str(raw.get("primary_layer") or "").strip().lower(),
        change_details=_parse_change_details(raw.get("change_details")),
        change_scope=str(raw.get("change_scope") or "").strip(),
        requirement_summary=str(raw.get("requirement_summary") or "").strip(),
        requirement_type=str(raw.get("requirement_type") or "").strip(),
        progress=_parse_change_progress(content, raw),
        raw_metadata=raw,
    )


def find_pending_reopen_target(docs_dir: Path) -> ReopenTarget | None:
    """取最新未 completed 且 run_mode=reopen 的 change。"""
    for _version, path in reversed(list_change_files(docs_dir)):
        target = parse_reopen_target(path)
        if target is None or target.run_mode != "reopen":
            continue
        if target.progress.status == "completed":
            continue
        return target
    return None


def resolve_reopen_target(docs_dir: Path) -> ReopenTarget:
    baseline = baseline_requirements_path(docs_dir)
    if baseline is None:
        raise ValueError(
            f"缺少基线需求 {requirements_dir(docs_dir) / BASE_REQUIREMENTS}，"
            "请先完成首轮 sddl start"
        )
    target = find_pending_reopen_target(docs_dir)
    if target is None:
        nxt = next_change_version(docs_dir)
        hint = ""
        latest = list_change_files(docs_dir)
        if latest:
            last = parse_reopen_target(latest[-1][1])
            if last and last.progress.status == "completed":
                hint = (
                    f"。最新 {latest[-1][1].name} 已 completed，"
                    f"请用 reopen-workflow 新建 `changes/{change_file_name(nxt)}`"
                )
        raise ValueError(
            "未找到可 reopen 的变更文件。请先用 reopen-workflow skill 写入 "
            f"`changes/{change_file_name(nxt)}`（含 run_mode: reopen 与 change_details），"
            "再执行 sddl reopen"
            + hint
        )
    return target


def update_change_progress(
    path: Path,
    *,
    status: str | None = None,
    last_node: str | None = None,
    all_docs_updated: bool | None = None,
    note: str | None = None,
) -> None:
    """原地更新 change 文件中的 ``change_status`` 块（没有则追加）。"""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    current = _parse_change_progress(content, _extract_metadata_raw(content))
    if status is not None:
        current.status = status
    if last_node is not None:
        current.last_node = last_node
    if all_docs_updated is not None:
        current.all_docs_updated = all_docs_updated
    if note is not None:
        current.note = note
    current.updated_at = datetime.now(timezone.utc).isoformat()

    block = (
        "```yaml change_status\n"
        + yaml.safe_dump(current.to_dict(), allow_unicode=True, sort_keys=False).strip()
        + "\n```"
    )
    pattern = r"```yaml\s+change_status\s*\n.*?```"
    if re.search(pattern, content, re.DOTALL | re.IGNORECASE):
        content = re.sub(
            pattern, block, content, count=1, flags=re.DOTALL | re.IGNORECASE
        )
    else:
        meta_pat = r"(```yaml\s+change_metadata\s*\n.*?```)"
        if re.search(meta_pat, content, re.DOTALL | re.IGNORECASE):
            content = re.sub(
                meta_pat,
                rf"\1\n\n{block}",
                content,
                count=1,
                flags=re.DOTALL | re.IGNORECASE,
            )
        else:
            content = content.rstrip() + "\n\n" + block + "\n"
    path.write_text(
        content if content.endswith("\n") else content + "\n", encoding="utf-8"
    )


def _set_approval_pending(content: str, approval_block: str) -> str:
    pattern = (
        rf"(```yaml\s+{re.escape(approval_block)}\s*\n)"
        rf"(.*?)(```)"
    )
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if not match:
        return content

    body = match.group(2)
    if re.search(r"(?m)^status:\s*", body):
        body = re.sub(r"(?m)^status:\s*.*$", "status: pending", body, count=1)
    else:
        body = "status: pending\n" + body
    if re.search(r"(?m)^confirmed_at:\s*", body):
        body = re.sub(r"(?m)^confirmed_at:\s*.*$", 'confirmed_at: ""', body, count=1)
    if re.search(r"(?m)^user_note:\s*", body):
        body = re.sub(
            r"(?m)^user_note:\s*.*$",
            'user_note: "reopen: awaiting re-check"',
            body,
            count=1,
        )
    else:
        body = body.rstrip() + '\nuser_note: "reopen: awaiting re-check"\n'
    return (
        content[: match.start()]
        + match.group(1)
        + body
        + match.group(3)
        + content[match.end() :]
    )


def _collect_stage_paths(docs_dir: Path, node_id: str, role: str = "") -> list[Path]:
    cfg = PHASE_CONFIGS.get(node_id)
    if not cfg:
        return []
    paths: list[Path] = []
    if cfg.final_file in ROLE_SCOPED_OUTPUTS:
        if role in ("backend", "frontend", "both"):
            found = find_output(docs_dir, cfg.final_file, role)
            if found:
                paths.append(found)
        else:
            paths.extend(list_role_outputs(docs_dir, cfg.final_file))
    else:
        found = find_output(docs_dir, cfg.final_file)
        if found:
            paths.append(found)
    return paths


def reset_downstream_approvals(
    docs_dir: Path, *, role: str, after_node: str
) -> list[str]:
    """某节点 impact=patch 后：仅将该节点之后的 approval 打回 pending。"""
    try:
        idx = REOPEN_CASCADE.index(after_node)
    except ValueError:
        idx = -1
    targets = REOPEN_CASCADE[idx + 1 :]
    touched: list[str] = []
    for node_id in targets:
        cfg = PHASE_CONFIGS.get(node_id)
        if not cfg:
            continue
        for path in _collect_stage_paths(docs_dir, node_id, role):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            approval = parse_approval(text, cfg.approval_block)
            if approval.status == "pending" or not approval.is_approved:
                continue
            new_text = _set_approval_pending(text, cfg.approval_block)
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                touched.append(str(path))
                logger.info(
                    "reopen：节点 %s 有改动 → 下游 %s 的 %s → pending",
                    after_node,
                    path.name,
                    cfg.approval_block,
                )
    return touched


def reset_all_stage_approvals(docs_dir: Path) -> list[str]:
    """reopen 首次启动时：把所有已存在阶段文档的 approval 一律打回 pending。"""
    touched: list[str] = []
    for node_id in REOPEN_CASCADE:
        cfg = PHASE_CONFIGS.get(node_id)
        if not cfg or not cfg.approval_block:
            continue
        for path in _collect_stage_paths(docs_dir, node_id, role=""):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if cfg.approval_block not in text:
                continue
            approval = parse_approval(text, cfg.approval_block)
            if approval.status == "pending":
                continue
            new_text = _set_approval_pending(text, cfg.approval_block)
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")
                touched.append(str(path))
                logger.info(
                    "reopen 启动：%s 的 %s → pending",
                    path.name,
                    cfg.approval_block,
                )
    return touched


@dataclass
class ReopenStartPlan:
    """根据各节点 md 的 reviewed_changes 决定本次 reopen 从哪开始。"""

    start_node: str
    fresh: bool  # True=尚无任何节点复核过本 change：全量 pending
    last_node: str = ""  # 仅作日志：已复核到的最后一个节点
    note: str = ""


def parse_reviewed_changes(content: str) -> list[str]:
    """从 ``reopen_impact.reviewed_changes`` 读取已复核的 change 列表。"""
    data = parse_reopen_impact(content)
    raw = data.get(REVIEWED_CHANGES_KEY)
    found: list[str] = []
    if isinstance(raw, list):
        found.extend(str(x).strip() for x in raw if str(x).strip())
    elif isinstance(raw, str) and raw.strip():
        found.append(raw.strip())

    seen: set[str] = set()
    out: list[str] = []
    for item in found:
        key = item.replace("\\", "/")
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def content_has_reviewed(content: str, doc_ref: str) -> bool:
    ref = doc_ref.replace("\\", "/").strip()
    reviewed = parse_reviewed_changes(content)
    if ref in reviewed:
        return True
    short = ref.rsplit("/", 1)[-1]
    return short in reviewed or any(r.endswith("/" + short) or r == short for r in reviewed)


def upsert_reviewed_changes(content: str, doc_refs: list[str]) -> str:
    """把 doc_refs 合并进 ``reopen_impact.reviewed_changes``（无该块则创建）。"""
    wanted = [r.replace("\\", "/").strip() for r in doc_refs if r and str(r).strip()]
    if not wanted:
        return content

    current = parse_reviewed_changes(content)
    merged: list[str] = []
    seen: set[str] = set()
    for item in current + wanted:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)

    pattern = r"(```yaml\s+reopen_impact\s*\n)(.*?)(```)"
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            data = yaml.safe_load(match.group(2))
        except yaml.YAMLError:
            data = None
        if not isinstance(data, dict):
            data = {}
        else:
            data = dict(data)
        data[REVIEWED_CHANGES_KEY] = merged
        body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip() + "\n"
        return (
            content[: match.start()]
            + match.group(1)
            + body
            + match.group(3)
            + content[match.end() :]
        )

    block = (
        "```yaml reopen_impact\n"
        + yaml.safe_dump(
            {
                "impact": "none",
                "summary": "engine: recorded reviewed_changes",
                "touched_anchors": [],
                REVIEWED_CHANGES_KEY: merged,
            },
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        + "\n```"
    )
    # 必须插在 Workflow 状态之前：finalize 会从该标题起整段剥离再重写状态表
    status_heading = "## Workflow 状态"
    if status_heading in content:
        head, tail = content.split(status_heading, 1)
        return head.rstrip() + "\n\n" + block + "\n\n" + status_heading + tail
    return content.rstrip() + "\n\n" + block + "\n"


def write_reviewed_changes(path: Path, doc_refs: list[str]) -> bool:
    """原地合并 reviewed_changes；有改动返回 True。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    new_text = upsert_reviewed_changes(text, doc_refs)
    if new_text == text:
        return False
    path.write_text(
        new_text if new_text.endswith("\n") else new_text + "\n", encoding="utf-8"
    )
    return True


def _node_paths_for_review(docs_dir: Path, node_id: str, role: str = "") -> list[Path]:
    if node_id == "identify_intent":
        from .router import intent_local_path

        path = intent_local_path(docs_dir)
        return [path] if path.is_file() else []
    return _collect_stage_paths(docs_dir, node_id, role)


_REOPEN_GRAPH_ORDER = (
    "analyze_requirements",
    "plan_session",
    "split_tasks",
    "implement_code",
    "verify_tests",
    "ai_review",
    "commit_code",
)


def node_had_prior_output(docs_dir: Path, node_id: str, *, role: str = "") -> bool:
    """进入该节点时，磁盘上是否已有阶段文档（有则注入 reopen，无则常规交付）。"""
    if node_id == "identify_intent":
        return False
    if node_id == "plan_session":
        if _collect_stage_paths(docs_dir, "plan_session", role):
            return True
        return bool(_collect_stage_paths(docs_dir, "design_test_cases", role))
    paths = _node_paths_for_review(docs_dir, node_id, role)
    return any(p.is_file() for p in paths)


def reopen_applies_to_node(
    state: dict[str, Any], docs_dir: Path, node_id: str
) -> bool:
    """本节点是否按 reopen 注入/门禁执行：进节点时看有没有已有产出即可。"""
    if str(state.get("run_mode") or "").strip().lower() != "reopen":
        return False
    if node_id == "identify_intent":
        return False
    role = str(state.get("delivery_role") or "")
    return node_had_prior_output(docs_dir, node_id, role=role)


def next_reopen_frontier_exhausted(
    docs_dir: Path, after_node: str, *, role: str = ""
) -> bool:
    """``after_node`` 之后的图节点当前是否都还没有阶段文档。"""
    try:
        idx = _REOPEN_GRAPH_ORDER.index(after_node)
    except ValueError:
        return False
    for node_id in _REOPEN_GRAPH_ORDER[idx + 1 :]:
        if node_had_prior_output(docs_dir, node_id, role=role):
            return False
    return True


def _node_reviewed_current(
    docs_dir: Path, node_id: str, change_ref: str, *, role: str = ""
) -> bool:
    paths = _node_paths_for_review(docs_dir, node_id, role)
    if not paths:
        return False
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        if not content_has_reviewed(text, change_ref):
            return False
    return True


def _phase_ready_for_skip(
    docs_dir: Path, node_id: str, *, role: str = ""
) -> bool:
    from .phase_gate import phase_complete, plan_session_complete, requirements_complete

    state: dict[str, Any] = {"run_mode": "reopen", "auto": False}
    if role:
        state["delivery_role"] = role
    if node_id == "analyze_requirements":
        return requirements_complete(docs_dir, state)
    if node_id == "identify_intent":
        return True
    if node_id == "plan_session":
        return plan_session_complete(docs_dir, state)
    if node_id == "design_test_cases":
        from .phase_gate import phase_content_ready

        return phase_content_ready(docs_dir, "design_test_cases", state)
    if node_id in PHASE_CONFIGS:
        return phase_complete(docs_dir, node_id, state)
    return False


def resolve_reopen_start(
    target: ReopenTarget,
    docs_dir: Path,
    *,
    has_intent_cache: bool,
    role: str = "",
) -> ReopenStartPlan:
    """按各节点 md 的 ``reviewed_changes`` 决定从哪续跑（不依赖 change 的 last_node）。"""
    change_ref = change_doc_ref(target.logical_name)
    # 扫描顺序：图节点；有意图缓存则跳过 identify_intent
    scan: list[str] = [
        "analyze_requirements",
        "plan_session",
        "split_tasks",
        "implement_code",
        "verify_tests",
        "ai_review",
        "commit_code",
    ]
    if not has_intent_cache:
        scan = [
            "analyze_requirements",
            "identify_intent",
            "plan_session",
            "split_tasks",
            "implement_code",
            "verify_tests",
            "ai_review",
            "commit_code",
        ]

    any_reviewed = False
    last_ok = ""
    for node_id in scan:
        # plan_session 还要用例文件也记下本 change（若已有用例文件）
        reviewed = _node_reviewed_current(docs_dir, node_id, change_ref, role=role)
        if node_id == "plan_session":
            tc_paths = _collect_stage_paths(docs_dir, "design_test_cases", role)
            if tc_paths:
                reviewed = reviewed and _node_reviewed_current(
                    docs_dir, "design_test_cases", change_ref, role=role
                )

        if not reviewed:
            # 尚无产出：若前面也没人复核过本 change → 首次全量
            paths = _node_paths_for_review(docs_dir, node_id, role)
            if not paths and not any_reviewed:
                return ReopenStartPlan(
                    start_node="analyze_requirements",
                    fresh=True,
                    last_node="",
                    note=f"no node reviewed {change_ref} yet → fresh",
                )
            start = node_id
            if node_id == "identify_intent" and has_intent_cache:
                start = "plan_session"
            return ReopenStartPlan(
                start_node=start,
                fresh=not any_reviewed,
                last_node=last_ok,
                note=(
                    f"resume by reviewed_changes: stop before {node_id} "
                    f"(missing {change_ref})"
                ),
            )

        if not _phase_ready_for_skip(docs_dir, node_id, role=role):
            return ReopenStartPlan(
                start_node=node_id,
                fresh=False,
                last_node=last_ok,
                note=f"resume retry {node_id} (reviewed but phase incomplete)",
            )

        any_reviewed = True
        last_ok = node_id

    return ReopenStartPlan(
        start_node="commit_code",
        fresh=False,
        last_node=last_ok or "commit_code",
        note=f"all nodes reviewed {change_ref}; land on commit_code",
    )


def parse_reopen_impact(content: str) -> dict[str, Any]:
    match = re.search(
        r"```yaml\s+reopen_impact\s*\n(.*?)```",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def reopen_impact_is_none(content: str) -> bool:
    data = parse_reopen_impact(content)
    impact = str(data.get("impact") or "").strip().lower()
    return impact in {"none", "no", "无", "无影响"}


def reopen_inject_path() -> str:
    return REOPEN_INJECT_PROMPT


def format_reopen_impact_text(data: dict[str, Any], *, node_label: str = "") -> str:
    if not data:
        return "（上一节点未写 reopen_impact）"
    impact = str(data.get("impact") or "").strip() or "（未标）"
    summary = str(data.get("summary") or "").strip() or "（无摘要）"
    anchors = data.get("touched_anchors") or []
    if isinstance(anchors, list):
        anchor_lines = "\n".join(f"  - {a}" for a in anchors if str(a).strip()) or "  - （无）"
    else:
        anchor_lines = f"  - {anchors}"
    head = f"上一节点（{node_label}）" if node_label else "上一节点"
    return (
        f"- {head} impact: `{impact}`\n"
        f"- 摘要: {summary}\n"
        f"- 改过的位置:\n{anchor_lines}"
    )


def load_upstream_reopen_impact(
    docs_dir: Path, node_id: str, state: dict[str, Any]
) -> str:
    """读上一节点产出里的 reopen_impact，供本节点快速定位。"""
    upstream = UPSTREAM_NODE.get(node_id)
    if not upstream:
        return "（本节点无上游变更摘要：对照 change 文件即可）"
    cfg = PHASE_CONFIGS.get(upstream)
    if not cfg:
        return "（无上游）"
    role = str(state.get("delivery_role") or "")
    lookup = role if cfg.final_file in ROLE_SCOPED_OUTPUTS else None
    if lookup in ("", "unknown"):
        lookup = None
    path = find_output(docs_dir, cfg.final_file, lookup)
    if path is None or not path.is_file():
        return f"（上游节点 `{upstream}` 尚无产出文件）"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return f"（无法读取上游 `{upstream}` 产出）"
    data = parse_reopen_impact(content)
    return format_reopen_impact_text(data, node_label=upstream)


# 兼容旧测试名
list_change_requirement_files = list_change_files
change_requirements_logical = change_file_name
