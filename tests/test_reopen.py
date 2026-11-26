"""reopen：changes/change-vN、注入、门禁。"""

from __future__ import annotations

from pathlib import Path

from src.config_loader import NodeConfig
from src.phase_gate import phase_complete, requirements_logical_name
from src.reopen import (
    change_doc_ref,
    change_file_name,
    find_pending_reopen_target,
    list_change_files,
    load_upstream_reopen_impact,
    next_change_version,
    next_reopen_frontier_exhausted,
    node_had_prior_output,
    parse_reviewed_changes,
    parse_reopen_target,
    reopen_applies_to_node,
    reopen_impact_is_none,
    reopen_inject_path,
    reset_all_stage_approvals,
    reset_downstream_approvals,
    resolve_reopen_start,
    resolve_reopen_target,
    update_change_progress,
    upsert_reviewed_changes,
    write_reviewed_changes,
)


def _change_body(
    *,
    run_mode: str = "reopen",
    version: str = "v1",
    primary_layer: str = "plan",
    status: str = "pending",
) -> str:
    return "\n".join(
        [
            f"# 变更 {version}",
            "",
            "```yaml change_metadata",
            f"run_mode: {run_mode}",
            f"change_version: {version}",
            "change_scope: 登录失败提示",
            f"primary_layer: {primary_layer}",
            "change_details:",
            "  - id: C1",
            "    summary: 修改登录失败提示文案",
            "    locate_hint: requirements",
            "    anchor: US-1",
            "```",
            "",
            "```yaml change_status",
            f"status: {status}",
            'last_node: ""',
            "all_docs_updated: false",
            'updated_at: ""',
            'note: ""',
            "```",
            "",
            "## 变更说明",
            "用户反馈提示不清晰。",
            "",
        ]
    )


def _req_approved(*, impact: str | None = "none") -> str:
    impact_block = ""
    if impact is not None:
        impact_block = (
            f"\n```yaml reopen_impact\nimpact: {impact}\nsummary: ok\n"
            "touched_anchors: []\n```\n"
        )
    return "\n".join(
        [
            "## A. 现状与影响",
            "这是足够长的需求正文，用来满足阶段门禁对正文长度与结构的要求。" * 4,
            "",
            "## B. 业务方案",
            "业务方案段落同样需要足够长度，包含用户故事与验收口径的占位描述。" * 3,
            "",
            "```yaml requirement_metadata",
            "requirement_type: existing_change",
            "requirement_summary: 登录",
            "change_scope: auth",
            "affected_modules:",
            "  - auth",
            "compatibility_risk: low",
            "```",
            "",
            "```yaml clarification_checklist",
            "all_resolved: true",
            "pending_count: 0",
            "items: []",
            "```",
            "",
            "```yaml requirement_approval",
            "status: pending",
            'confirmed_at: ""',
            'user_note: ""',
            "```",
            impact_block,
        ]
    )


def test_list_and_next_version_in_changes(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text("baseline", encoding="utf-8")
    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "change-v1.md").write_text("v1", encoding="utf-8")
    (changes / "change-v2.md").write_text("v2", encoding="utf-8")

    versions = list_change_files(tmp_path)
    assert [v for v, _ in versions] == ["v1", "v2"]
    assert next_change_version(tmp_path) == "v3"
    assert change_file_name("v1") == "change-v1.md"


def test_resolve_reopen_target_from_changes(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text("baseline", encoding="utf-8")
    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "change-v1.md").write_text(
        _change_body(run_mode="initial"), encoding="utf-8"
    )
    assert find_pending_reopen_target(tmp_path) is None

    (changes / "change-v2.md").write_text(
        _change_body(run_mode="reopen", version="v2"), encoding="utf-8"
    )
    target = resolve_reopen_target(tmp_path)
    assert target.version == "v2"
    assert target.logical_name == "change-v2.md"
    assert target.primary_layer == "plan"
    assert target.change_details[0].anchor == "US-1"


def test_reopen_keeps_normal_prompt_and_inject() -> None:
    node = NodeConfig(
        node_id="analyze_requirements",
        name="需求分析",
        tool="echo",
        prompt_file="prompts/01_requirements.md",
        prompt_file_auto="prompts/01_requirements.auto.md",
        output_file="01-requirements.md",
    )
    assert node.resolve_prompt_file(False) == "prompts/01_requirements.md"
    inject = Path(__file__).resolve().parents[1] / reopen_inject_path()
    assert inject.is_file()
    text = inject.read_text(encoding="utf-8")
    assert "upstream_reopen_impact" in text
    assert "change_details" in text
    assert "以下是本节点常规阶段提示词" in text
    assert "reopen_impact.reviewed_changes" in text or "reviewed_changes" in text
    assert "禁止一味追加" in text
    assert "就地改" in text or "原文位置" in text


def test_requirements_logical_name_always_baseline() -> None:
    assert requirements_logical_name({}) == "01-requirements.md"
    assert (
        requirements_logical_name({"run_mode": "reopen", "change_version": "v2"})
        == "01-requirements.md"
    )


def test_reopen_impact_none_gate(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    path = req / "01-requirements.md"
    path.write_text(_req_approved(impact="none"), encoding="utf-8")
    state = {"run_mode": "reopen", "auto": False}
    assert reopen_impact_is_none(path.read_text(encoding="utf-8"))
    assert phase_complete(tmp_path, "analyze_requirements", state)


def test_upstream_reopen_impact(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text(
        _req_approved(impact="patch").replace(
            "touched_anchors: []",
            'touched_anchors:\n  - "US-1"',
        ),
        encoding="utf-8",
    )
    text = load_upstream_reopen_impact(
        tmp_path, "plan_session", {"delivery_role": "backend"}
    )
    assert "US-1" in text
    assert "patch" in text


def test_reset_downstream_approvals(tmp_path: Path) -> None:
    plan = tmp_path / "plan"
    plan.mkdir()
    plan_file = plan / "02-plan-backend.md"
    plan_file.write_text(
        "\n".join(
            [
                "# plan",
                "",
                "```yaml plan_approval",
                "status: approved",
                "confirmed_at: 2026-01-01T00:00:00",
                "user_note: ok",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    touched = reset_downstream_approvals(
        tmp_path, role="backend", after_node="analyze_requirements"
    )
    assert touched
    assert "status: pending" in plan_file.read_text(encoding="utf-8")


def test_reset_all_stage_approvals(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    req_file = req / "01-requirements.md"
    req_file.write_text(
        "\n".join(
            [
                "# req",
                "",
                "```yaml requirement_approval",
                "status: approved",
                "confirmed_at: 2026-01-01T00:00:00",
                "user_note: ok",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plan = tmp_path / "plan"
    plan.mkdir()
    plan_file = plan / "02-plan-backend.md"
    plan_file.write_text(
        "\n".join(
            [
                "# plan",
                "",
                "```yaml plan_approval",
                "status: approved",
                "confirmed_at: 2026-01-01T00:00:00",
                "user_note: ok",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    touched = reset_all_stage_approvals(tmp_path)
    assert len(touched) >= 2
    assert "status: pending" in req_file.read_text(encoding="utf-8")
    assert "status: pending" in plan_file.read_text(encoding="utf-8")


def test_reopen_only_when_prior_stage_docs_exist(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text("# req\n", encoding="utf-8")
    tasks = tmp_path / "task"
    tasks.mkdir()
    (tasks / "03-tasks-backend.md").write_text("# tasks\n", encoding="utf-8")

    state = {"run_mode": "reopen", "delivery_role": "backend"}
    assert node_had_prior_output(tmp_path, "analyze_requirements", role="backend")
    assert node_had_prior_output(tmp_path, "split_tasks", role="backend")
    assert not node_had_prior_output(tmp_path, "implement_code", role="backend")
    assert reopen_applies_to_node(state, tmp_path, "split_tasks")
    assert not reopen_applies_to_node(state, tmp_path, "implement_code")
    assert next_reopen_frontier_exhausted(tmp_path, "split_tasks", role="backend")
    assert not next_reopen_frontier_exhausted(
        tmp_path, "analyze_requirements", role="backend"
    )


def test_reviewed_changes_in_reopen_impact() -> None:
    body = "\n".join(
        [
            "# req",
            "",
            "```yaml reopen_impact",
            "impact: none",
            "summary: ok",
            "touched_anchors: []",
            "```",
            "",
        ]
    )
    updated = upsert_reviewed_changes(body, [change_doc_ref("v1")])
    reviewed = parse_reviewed_changes(updated)
    assert reviewed == ["changes/change-v1.md"]
    assert "reopen_impact" in updated
    # 再合并同一项不重复
    again = upsert_reviewed_changes(updated, [change_doc_ref("change-v1.md")])
    assert parse_reviewed_changes(again).count("changes/change-v1.md") == 1
    # 写入时落在 reopen_impact 内
    assert "reviewed_changes:" in again.split("```yaml reopen_impact")[1].split("```")[0]


def test_reviewed_changes_inserted_before_workflow_status() -> None:
    """initial-tail 节点常已有 Workflow 状态；追加块必须在状态表之前，否则 finalize 剥离会丢掉。"""
    from src.nodes.node_runner import NodeRunner
    from src.parsers.workflow_status_parser import WorkflowStatus, upsert_workflow_status

    body = "\n".join(
        [
            "# 任务拆分（后端）",
            "",
            "```yaml tasks_approval",
            "status: approved",
            "```",
            "",
            "## Workflow 状态",
            "",
            "| 字段 | 值 |",
            "|------|-----|",
            "| node | split_tasks |",
            "",
        ]
    )
    stamped = upsert_reviewed_changes(body, [change_doc_ref("v1")])
    assert "reopen_impact" in stamped
    assert stamped.find("reopen_impact") < stamped.find("## Workflow 状态")
    assert parse_reviewed_changes(stamped) == ["changes/change-v1.md"]

    # 模拟 finalize：剥离状态表再写回，reviewed_changes 仍在
    stripped = NodeRunner._strip_existing_status(stamped)
    assert "reopen_impact" in stripped
    final = upsert_workflow_status(
        stripped,
        WorkflowStatus(
            node="split_tasks",
            status="completed",
            next_node="implement_code",
            phase="tasks",
        ),
    )
    assert parse_reviewed_changes(final) == ["changes/change-v1.md"]
    assert final.find("reopen_impact") < final.find("## Workflow 状态")


def test_change_temp_subdir_survives_initial_tail() -> None:
    from src.reopen import change_temp_subdir

    assert change_temp_subdir({"run_mode": "reopen", "change_version": "v1"}) == "change-v1"
    # reopen → initial 后仍按 change_version 落 temp-prompts/change-vN
    assert (
        change_temp_subdir({"run_mode": "initial", "change_version": "v1"}) == "change-v1"
    )
    assert (
        change_temp_subdir(
            {
                "run_mode": "initial",
                "change_version": "",
                "node_outputs": {"change_file": "docs/changes/change-v2.md"},
            }
        )
        == "change-v2"
    )
    assert change_temp_subdir({"run_mode": "initial"}) == ""


def _pad(text: str, min_chars: int = 220) -> str:
    if len(text) >= min_chars:
        return text
    return text + ("\n\n" + ("x" * (min_chars - len(text))))


def _approved_plan_backend() -> str:
    return _pad(
        "\n".join(
            [
                "# 计划（后端）",
                "",
                "```yaml plan_metadata",
                "delivery_role: backend",
                "delivery_blocks:",
                "  - backend",
                'role_basis: "pom"',
                "```",
                "",
                "```yaml clarification_checklist",
                "all_resolved: true",
                "pending_count: 0",
                "items: []",
                "```",
                "",
                "```yaml plan_approval",
                "status: approved",
                "```",
                "",
                "```yaml reopen_impact",
                "impact: patch",
                "summary: ok",
                "touched_anchors: []",
                "```",
                "",
                "## 改动入口",
                "登录契约与验证码链路。",
                "",
                "## 核心实现",
                "按 change 调整认证控制器与服务。",
                "",
            ]
        )
    )


def _approved_test_cases() -> str:
    return _pad(
        "\n".join(
            [
                "# 测试用例",
                "",
                "## 覆盖",
                "登录成功与失败主路径。",
                "",
                "```yaml test_cases_approval",
                "status: pending",
                "```",
                "",
            ]
        )
    )


def _approved_tasks_backend(*, with_status: bool = True) -> str:
    lines = [
        "# 任务拆分（后端）",
        "",
        "```yaml clarification_checklist",
        "all_resolved: true",
        "pending_count: 0",
        "items: []",
        "```",
        "",
        "```yaml tasks_approval",
        "status: approved",
        "```",
        "",
        "## 任务总览",
        "T-B1 登录仅用户名密码；T-B2 删除图形验证码整条链路。",
        "",
        "## 依赖与执行顺序",
        "先 T-B1 再 T-B2，实施起点为当前工作区。",
        "",
    ]
    if with_status:
        lines.extend(
            [
                "## Workflow 状态",
                "",
                "| 字段 | 值 |",
                "|------|-----|",
                "| node | split_tasks |",
                "| status | completed |",
                "| next_node | implement_code |",
                "| phase | tasks |",
                "| pending_count | 0 |",
                "| all_resolved | true |",
                "",
            ]
        )
    return _pad("\n".join(lines))


def test_resolve_reopen_start_by_node_reviewed_changes(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    req_file = req / "01-requirements.md"
    req_file.write_text(
        _req_approved(impact="none").replace(
            "status: pending",
            "status: approved",
        ),
        encoding="utf-8",
    )
    changes = tmp_path / "changes"
    changes.mkdir()
    path = changes / "change-v1.md"
    path.write_text(_change_body(status="in_progress"), encoding="utf-8")
    target = parse_reopen_target(path)
    assert target is not None

    # 尚无节点写入本 change → 首次
    fresh = resolve_reopen_start(
        target, tmp_path, has_intent_cache=True, role="backend"
    )
    assert fresh.fresh is True
    assert fresh.start_node == "analyze_requirements"

    # 需求节点已复核 change-v1 → 续跑到 plan（有意图缓存）
    write_reviewed_changes(req_file, ["changes/change-v1.md"])
    plan = resolve_reopen_start(
        target, tmp_path, has_intent_cache=True, role="backend"
    )
    assert plan.fresh is False
    assert plan.start_node == "plan_session"

    # 无意图缓存 → 先走 identify_intent
    no_cache = resolve_reopen_start(
        target, tmp_path, has_intent_cache=False, role=""
    )
    assert no_cache.fresh is False
    assert no_cache.start_node == "identify_intent"

    # change 文件的 last_node 不应驱动续跑
    update_change_progress(
        path,
        status="in_progress",
        last_node="commit_code",
        all_docs_updated=False,
    )
    target2 = parse_reopen_target(path)
    assert target2 is not None
    still = resolve_reopen_start(
        target2, tmp_path, has_intent_cache=True, role="backend"
    )
    assert still.start_node == "plan_session"


def test_resolve_reopen_start_reads_reopen_impact_on_initial_tail(
    tmp_path: Path,
) -> None:
    """续跑只认 reopen_impact.reviewed_changes；initial-tail 引擎补块也要能跳过。"""
    req = tmp_path / "requirements"
    req.mkdir()
    req_file = req / "01-requirements.md"
    req_file.write_text(
        _req_approved(impact="none").replace(
            "status: pending",
            "status: approved",
        ),
        encoding="utf-8",
    )
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    plan_file = plan_dir / "02-plan-backend.md"
    plan_file.write_text(_approved_plan_backend(), encoding="utf-8")
    tc_file = plan_dir / "02-test-cases.md"
    tc_file.write_text(_approved_test_cases(), encoding="utf-8")

    changes = tmp_path / "changes"
    changes.mkdir()
    change_path = changes / "change-v1.md"
    change_path.write_text(
        _change_body(status="in_progress"),
        encoding="utf-8",
    )
    target = parse_reopen_target(change_path)
    assert target is not None

    write_reviewed_changes(req_file, ["changes/change-v1.md"])
    write_reviewed_changes(plan_file, ["changes/change-v1.md"])
    write_reviewed_changes(tc_file, ["changes/change-v1.md"])

    # 有计划戳、无任务文件 → 从 split_tasks 续（不看 change last_node）
    update_change_progress(
        change_path,
        status="in_progress",
        last_node="implement_code",
        all_docs_updated=True,
        note="noise",
    )
    mid = resolve_reopen_start(
        parse_reopen_target(change_path),  # type: ignore[arg-type]
        tmp_path,
        has_intent_cache=True,
        role="backend",
    )
    assert mid.start_node == "split_tasks"
    assert mid.fresh is False

    # 引擎给 tasks 补 reopen_impact.reviewed_changes（插在 Workflow 状态前）
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task_file = task_dir / "03-tasks-backend.md"
    task_file.write_text(
        upsert_reviewed_changes(
            _approved_tasks_backend(with_status=True),
            ["changes/change-v1.md"],
        ),
        encoding="utf-8",
    )
    assert "```yaml reopen_impact" in task_file.read_text(encoding="utf-8")
    assert parse_reviewed_changes(task_file.read_text(encoding="utf-8")) == [
        "changes/change-v1.md"
    ]

    after_tasks = resolve_reopen_start(
        parse_reopen_target(change_path),  # type: ignore[arg-type]
        tmp_path,
        has_intent_cache=True,
        role="backend",
    )
    assert after_tasks.start_node == "implement_code"
    assert after_tasks.last_node == "split_tasks"


def test_change_status_progress(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text("baseline", encoding="utf-8")
    changes = tmp_path / "changes"
    changes.mkdir()
    path = changes / "change-v1.md"
    path.write_text(_change_body(status="pending"), encoding="utf-8")

    target = parse_reopen_target(path)
    assert target is not None
    assert target.progress.status == "pending"
    assert target.progress.all_docs_updated is False

    update_change_progress(
        path,
        status="in_progress",
        last_node="plan_session",
        all_docs_updated=False,
        note="stopped after plan_session",
    )
    again = parse_reopen_target(path)
    assert again is not None
    assert again.progress.status == "in_progress"
    assert again.progress.last_node == "plan_session"
    assert "change_status" in path.read_text(encoding="utf-8")

    update_change_progress(
        path,
        status="completed",
        last_node="commit_code",
        all_docs_updated=True,
    )
    done = parse_reopen_target(path)
    assert done is not None
    assert done.progress.status == "completed"
    assert done.progress.all_docs_updated is True
    assert find_pending_reopen_target(tmp_path) is None
