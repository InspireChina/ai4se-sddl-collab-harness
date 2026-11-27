"""各阶段（需求 / 计划 / 任务）完成判定 — 内容就绪 vs 用户已确认。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .parsers.approval_parser import (
    parse_approval,
    parse_plan_metadata,
    parse_plan_role,
    read_approval_status,
)
from .parsers.clarification_parser import parse_clarification_checklist
from .parsers.requirement_parser import parse_requirement_metadata
from .parsers.test_report_parser import has_test_result_table
from .role_output import (
    ROLE_SCOPED_OUTPUTS,
    find_output,
    role_from_filename,
    write_output_path,
)
from .state import DevWorkflowState


@dataclass(frozen=True)
class PhaseConfig:
    node_id: str
    final_file: str
    approval_block: str
    approval_state_key: str
    next_node: str
    min_body_chars: int = 120
    requires_approval: bool = True
    # True：允许 clarification_checklist 仍有 pending（用例等人澄清，不挡计划会话结束）
    allow_unresolved_checklist: bool = False


def delivery_role_from_docs(docs_dir: Path, state: DevWorkflowState) -> str:
    """本次角色以这次运行确认后写入的 state 为准。"""
    role = str(state.get("delivery_role") or "")
    if role and role != "unknown":
        return role
    return ""


def requirements_logical_name(state: DevWorkflowState | None = None) -> str:
    """需求分析写入目标：始终是基线 ``01-requirements.md``（reopen 也是原地 patch）。

    变更点在 ``changes/change-vN.md``，不另写需求版本文件。
    """
    state = state or {}
    doc = str(state.get("requirements_doc") or "").strip()
    if doc and not doc.startswith("change-") and "change-v" not in doc:
        return doc
    return "01-requirements.md"


def _stage_file(docs_dir: Path, node_id: str, state: DevWorkflowState) -> Path | None:
    cfg = PHASE_CONFIGS.get(node_id)
    if not cfg:
        return None
    role = delivery_role_from_docs(docs_dir, state)
    final_file = cfg.final_file
    if node_id == "analyze_requirements":
        final_file = requirements_logical_name(state)
    lookup = (
        (role or None) if final_file in ROLE_SCOPED_OUTPUTS else None
    )
    return find_output(docs_dir, final_file, lookup)


def resolve_doc_path(
    docs_dir: Path, doc_key: str, role: str | None = None
) -> Path:
    lookup = None if not role or role == "unknown" else role
    found = find_output(docs_dir, doc_key, lookup)
    if found:
        return found
    return write_output_path(docs_dir, doc_key, lookup)


PHASE_CONFIGS: dict[str, PhaseConfig] = {
    "analyze_requirements": PhaseConfig(
        node_id="analyze_requirements",
        final_file="01-requirements.md",
        approval_block="requirement_approval",
        approval_state_key="requirement_approved",
        next_node="identify_intent",
        min_body_chars=200,
    ),
    "design_test_cases": PhaseConfig(
        node_id="design_test_cases",
        final_file="02-test-cases.md",
        approval_block="test_cases_approval",
        approval_state_key="test_cases_approved",
        next_node="plan_session",
        min_body_chars=200,
        requires_approval=False,
        allow_unresolved_checklist=True,
    ),
    "plan_session": PhaseConfig(
        node_id="plan_session",
        final_file="02-plan.md",
        approval_block="plan_approval",
        approval_state_key="plan_approved",
        next_node="split_tasks",
        min_body_chars=200,
    ),
    "split_tasks": PhaseConfig(
        node_id="split_tasks",
        final_file="03-tasks.md",
        approval_block="tasks_approval",
        approval_state_key="tasks_approved",
        next_node="implement_code",
        min_body_chars=200,
    ),
    "implement_code": PhaseConfig(
        node_id="implement_code",
        final_file="04-implementation.md",
        approval_block="implementation_approval",
        approval_state_key="implementation_approved",
        next_node="verify_tests",
        min_body_chars=120,
        requires_approval=False,
        allow_unresolved_checklist=True,
    ),
    "verify_tests": PhaseConfig(
        node_id="verify_tests",
        final_file="05-test-report.md",
        approval_block="test_report_approval",
        approval_state_key="test_report_approved",
        next_node="ai_review",
        min_body_chars=120,
        # 定稿块由本节点写 approved（完成标记），不等人二次点头
        requires_approval=False,
        allow_unresolved_checklist=True,
    ),
    "ai_review": PhaseConfig(
        node_id="ai_review",
        final_file="06-ai-review.md",
        approval_block="ai_review_approval",
        approval_state_key="ai_review_approved",
        next_node="commit_code",
        min_body_chars=120,
        requires_approval=False,
        allow_unresolved_checklist=True,
    ),
    "commit_code": PhaseConfig(
        node_id="commit_code",
        final_file="07-commit.md",
        approval_block="commit_approval",
        approval_state_key="commit_approved",
        next_node="",
        min_body_chars=80,
        requires_approval=False,
        allow_unresolved_checklist=True,
    ),
}


def get_phase_config(node_id: str) -> PhaseConfig | None:
    return PHASE_CONFIGS.get(node_id)


# 带澄清清单 + 人工定稿确认的节点：续跑时由引擎判定阶段并注入指令
_RESUME_GUIDED_NODES = frozenset(
    {"analyze_requirements", "plan_session", "split_tasks"}
)


@dataclass(frozen=True)
class PhaseResumeHint:
    """断点续跑阶段：由磁盘产出判定，注入「运行模式」头与第一章之间。

    auto：自决消解 pending / 直接写 approved（不复述文首整段无人值守开场白）；
    人工：向用户提问 / 等人确认后才写 approved。
    """

    stage: str  # fresh | clarify | await_approval
    pending_count: int = 0
    output_label: str = ""
    auto: bool = False

    @property
    def inject_markdown(self) -> str:
        if self.stage == "fresh":
            return ""
        label = self.output_label or "本节点产出文件"
        if self.stage == "clarify":
            if self.auto:
                return "\n".join(
                    [
                        f"> **断点续跑（引擎判定）**：`{label}` 已存在 → **初稿已完成**；"
                        f"澄清清单仍有 **{self.pending_count}** 条 pending。",
                        "> **禁止**推倒重写初稿全文、禁止从阶段 1 重来。",
                        "> **立即自决澄清**：对每条 `status: pending` 自行裁决并回写 "
                        "`resolved` + `answer`（写明依据）；**禁止**残留 pending、"
                        "**禁止**输出「请回复 / 请选择」。全部 resolved 后定稿，"
                        "并将 approval **直接写为 `approved`**。",
                        "",
                    ]
                )
            return "\n".join(
                [
                    f"> **断点续跑（引擎判定）**：`{label}` 已存在 → **初稿已完成**；"
                    f"澄清清单仍有 **{self.pending_count}** 条 pending。",
                    "> **禁止**推倒重写初稿全文、禁止从阶段 1 重来。",
                    "> **立即进入澄清**：只处理 `status: pending` 的项"
                    "（含推荐强度自动采纳）；向用户提问并等待答复，"
                    "全部 resolved 后再进入定稿确认（approval 仍保持 pending，"
                    "须用户明确确认后才写 `approved`）。",
                    "",
                ]
            )
        # await_approval
        if self.auto:
            return "\n".join(
                [
                    f"> **断点续跑（引擎判定）**：`{label}` 已存在；"
                    "澄清清单已全部 resolved（或无待澄清项）；"
                    "定稿确认块仍为 **pending**。",
                    "> **禁止**重写初稿、禁止再开澄清循环、禁止再扫一遍「阶段 1/2」。",
                    "> **立即定稿收尾**：核对要点后将 approval **直接写为 `approved`**"
                    "（可注明无人值守自动确认），**不要**再向用户展示确认菜单或等待回复。",
                    "",
                ]
            )
        return "\n".join(
            [
                f"> **断点续跑（引擎判定）**：`{label}` 已存在；"
                "澄清清单已全部 resolved（或无待澄清项）；"
                "定稿确认块仍为 **pending**。",
                "> **禁止**重写初稿、禁止再开澄清循环、禁止再扫一遍「阶段 1/2」。",
                "> **立即进入定稿确认**：展示定稿要点，请用户确认；"
                "要改则改完后 approval 仍写 pending，用户明确确认后再写 `approved`。"
                "**禁止**自行把 approval 写成 `approved`。",
                "",
            ]
        )


def insert_resume_inject(prompt: str, inject: str) -> str:
    """把续跑注入插到「运行模式」引用块之后、第一个章节之前。

    找不到「运行模式」头时退化为整段前置。
    """
    if not inject:
        return prompt
    lines = prompt.splitlines(keepends=True)
    start: int | None = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("> **运行模式"):
            start = i
            break
    if start is None:
        return inject + prompt

    end = start
    while end < len(lines) and lines[end].lstrip().startswith(">"):
        end += 1
    return "".join(lines[:end]) + "\n" + inject + "".join(lines[end:])


def resolve_phase_resume(
    docs_dir: Path, node_id: str, state: DevWorkflowState
) -> PhaseResumeHint | None:
    """根据已有阶段 md 判定续跑切入点；无产出或不适用的节点返回 None。"""
    if node_id not in _RESUME_GUIDED_NODES:
        return None
    cfg = PHASE_CONFIGS.get(node_id)
    if not cfg:
        return None
    auto = bool(state.get("auto"))
    path = _stage_file(docs_dir, node_id, state)
    if path is None or not path.is_file():
        return PhaseResumeHint(stage="fresh", auto=auto)

    content = path.read_text(encoding="utf-8")
    body = _body_without_status(content)
    if len(body) < 40:
        return PhaseResumeHint(stage="fresh", output_label=path.name, auto=auto)

    approval_status = read_approval_status(content, cfg.approval_block)
    checklist = parse_clarification_checklist(content)
    has_checklist_block = "```yaml clarification_checklist" in content
    has_structure = approval_status is not None or has_checklist_block
    if not has_structure:
        # 有正文但缺 YAML：仍视为已开过稿，优先去齐清单/确认，而不是当空白重来
        return PhaseResumeHint(
            stage="clarify",
            pending_count=max(checklist.pending_count, 1),
            output_label=path.name,
            auto=auto,
        )

    if approval_status == "approved":
        return PhaseResumeHint(stage="fresh", output_label=path.name, auto=auto)

    pending = checklist.pending_items()
    if pending or (has_checklist_block and not checklist.all_resolved):
        return PhaseResumeHint(
            stage="clarify",
            pending_count=len(pending) or checklist.pending_count,
            output_label=path.name,
            auto=auto,
        )

    # 文件在 + 澄清已齐 + approval 非 approved → 直接定稿确认
    return PhaseResumeHint(
        stage="await_approval",
        pending_count=0,
        output_label=path.name,
        auto=auto,
    )


def _body_without_status(content: str) -> str:
    if "## Workflow 状态" in content:
        content = content.split("## Workflow 状态", 1)[0]
    return content.strip()


def _checklist_ready(content: str, state: DevWorkflowState) -> bool:
    if state.get("auto"):
        return True
    checklist = parse_clarification_checklist(content)
    return checklist.all_resolved or not checklist.items


def _validate_requirements_final(content: str) -> bool:
    metadata = parse_requirement_metadata(content)
    # 仅接受规范化后的 new | existing_change（见 requirement_parser 别名表）
    return metadata.requirement_type in ("new", "existing_change")


def _validate_test_cases_final(content: str, cfg: PhaseConfig) -> bool:
    """用例写完的标志是确认块落成 pending 或 approved。

    pending：用例已经生成。approved：人已经确认。
    块还没写出来表示仍在写，不能放行。
    """
    if not _validate_generic_final(content, cfg):
        return False
    status = read_approval_status(content, "test_cases_approval")
    return status in ("pending", "approved")


def _validate_agent_done_approval(content: str, cfg: PhaseConfig) -> bool:
    """编码及之后节点：Agent 写完定稿块 ``status: approved`` 即视为本节点完成。"""
    if not _validate_generic_final(content, cfg):
        return False
    return read_approval_status(content, cfg.approval_block) == "approved"


def _validate_test_report_final(content: str, cfg: PhaseConfig) -> bool:
    """测试报告定稿：须有用例结果表，且 ``test_report_approval.status == approved``。"""
    if not _validate_agent_done_approval(content, cfg):
        return False
    return has_test_result_table(content)


def _validate_plan_final(content: str, cfg: PhaseConfig) -> bool:
    """计划定稿必须带 plan_metadata，后续节点才知道读哪些仓库规则。"""
    if not _validate_generic_final(content, cfg):
        return False
    present, _repos = parse_plan_metadata(content)
    return present


def _validate_generic_final(content: str, cfg: PhaseConfig) -> bool:
    body = _body_without_status(content)
    if len(body) < cfg.min_body_chars:
        return False
    return "##" in body or len(body.splitlines()) >= 8


CONTENT_VALIDATORS: dict[str, Callable[[str, PhaseConfig], bool]] = {
    "analyze_requirements": lambda content, cfg: _validate_generic_final(content, cfg)
    and _validate_requirements_final(content),
    "design_test_cases": _validate_test_cases_final,
    "plan_session": _validate_plan_final,
    "split_tasks": _validate_generic_final,
    "implement_code": _validate_agent_done_approval,
    "verify_tests": _validate_test_report_final,
    "ai_review": _validate_agent_done_approval,
    "commit_code": _validate_agent_done_approval,
}


def _role_file_ready(_docs_dir: Path, node_id: str, final_path: Path, content: str) -> bool:
    """计划文件必须带角色后缀，并且和文件里的角色一致。"""
    if node_id == "plan_session":
        file_role = role_from_filename(final_path.name)
        meta_role, _blocks, _basis = parse_plan_role(content, final_path.name)
        return bool(file_role) and meta_role == file_role
    return True


def phase_content_ready(docs_dir: Path, node_id: str, state: DevWorkflowState) -> bool:
    """定稿 md 结构完整（正文达标）；默认还要求澄清清单已消解。

    design_test_cases 允许澄清清单仍有 pending（allow_unresolved_checklist）。
    """
    cfg = PHASE_CONFIGS.get(node_id)
    if not cfg:
        return False

    final_path = _stage_file(docs_dir, node_id, state)
    if final_path is None:
        return False

    content = final_path.read_text(encoding="utf-8")
    if not cfg.allow_unresolved_checklist and not _checklist_ready(content, state):
        return False

    validator = CONTENT_VALIDATORS.get(node_id, _validate_generic_final)
    if not validator(content, cfg):
        return False
    return _role_file_ready(docs_dir, node_id, final_path, content)


def phase_user_approved(
    docs_dir: Path, node_id: str, state: DevWorkflowState | None = None
) -> bool:
    cfg = PHASE_CONFIGS.get(node_id)
    if not cfg:
        return False

    final_path = _stage_file(docs_dir, node_id, state or {})
    if final_path is None:
        return False

    approval = parse_approval(final_path.read_text(encoding="utf-8"), cfg.approval_block)
    return approval.is_approved


def phase_complete(docs_dir: Path, node_id: str, state: DevWorkflowState) -> bool:
    """定稿内容就绪且（若需要）用户已确认，可进入下一阶段。"""
    if not phase_content_ready(docs_dir, node_id, state):
        return False
    cfg = PHASE_CONFIGS.get(node_id)
    if cfg and not cfg.requires_approval:
        return True
    # --auto：内容就绪即过门禁，不等人写 approved
    if state.get("auto"):
        return True
    # reopen：无影响或无需澄清时允许直接过门禁（状态仍须由节点写成 approved）
    if str(state.get("run_mode") or "") == "reopen":
        from .reopen import reopen_impact_is_none

        final_path = _stage_file(docs_dir, node_id, state)
        if final_path is not None and final_path.is_file():
            content = final_path.read_text(encoding="utf-8")
            if reopen_impact_is_none(content) and _checklist_ready(content, state):
                return True
    return phase_user_approved(docs_dir, node_id, state)


def plan_session_complete(docs_dir: Path, state: DevWorkflowState) -> bool:
    """计划会话结束：`02-plan.md` 已确认，且用例已写完。

    用例写完的标志是 `test_cases_approval` 为 `pending`（已经生成）或 `approved`。
    不要求人已经把用例改成 `approved`。
    """
    if not phase_complete(docs_dir, "plan_session", state):
        return False
    return phase_content_ready(docs_dir, "design_test_cases", state)


def requirements_complete(docs_dir: Path, state: DevWorkflowState) -> bool:
    return phase_complete(docs_dir, "analyze_requirements", state)
