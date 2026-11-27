"""断点续跑：按已有阶段 md 判定 clarify / await_approval。"""

from __future__ import annotations

from pathlib import Path

from src.phase_gate import insert_resume_inject, resolve_phase_resume


def _req_doc(
    *,
    checklist_resolved: bool,
    approval: str = "pending",
    pending_items: int = 0,
) -> str:
    if checklist_resolved:
        checklist = "\n".join(
            [
                "```yaml clarification_checklist",
                "all_resolved: true",
                "pending_count: 0",
                "items: []",
                "```",
            ]
        )
    else:
        items = []
        for i in range(max(pending_items, 1)):
            items.extend(
                [
                    f"  - id: Q{i + 1}",
                    f'    question: "问题{i + 1}"',
                    "    status: pending",
                    '    answer: ""',
                ]
            )
        checklist = "\n".join(
            [
                "```yaml clarification_checklist",
                "all_resolved: false",
                f"pending_count: {max(pending_items, 1)}",
                "items:",
                *items,
                "```",
            ]
        )
    return "\n".join(
        [
            "# 需求分析",
            "",
            checklist,
            "",
            "```yaml requirement_approval",
            f"status: {approval}",
            'confirmed_at: ""',
            'user_note: ""',
            "```",
            "",
            "## 正文",
            "",
            "足够长的需求正文占位。" * 20,
            "",
        ]
    )


def test_resume_fresh_when_missing(tmp_path: Path) -> None:
    hint = resolve_phase_resume(tmp_path, "analyze_requirements", {})
    assert hint is not None
    assert hint.stage == "fresh"
    assert hint.inject_markdown == ""


def test_resume_clarify_when_pending_items(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text(
        _req_doc(checklist_resolved=False, pending_items=2), encoding="utf-8"
    )
    hint = resolve_phase_resume(tmp_path, "analyze_requirements", {})
    assert hint is not None
    assert hint.stage == "clarify"
    assert hint.pending_count == 2
    assert hint.auto is False
    assert "初稿已完成" in hint.inject_markdown
    assert "立即进入澄清" in hint.inject_markdown
    assert "推荐强度自动采纳" in hint.inject_markdown
    assert "向用户提问并等待答复" in hint.inject_markdown
    assert "立即自决澄清" not in hint.inject_markdown


def test_resume_clarify_auto_self_resolve(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text(
        _req_doc(checklist_resolved=False, pending_items=2), encoding="utf-8"
    )
    hint = resolve_phase_resume(
        tmp_path, "analyze_requirements", {"auto": True}
    )
    assert hint is not None
    assert hint.stage == "clarify"
    assert hint.auto is True
    text = hint.inject_markdown
    assert "立即自决澄清" in text
    assert "直接写为 `approved`" in text
    assert "禁止**残留 pending" in text or "禁止残留 pending" in text
    assert "向用户提问并等待答复" not in text
    assert "推荐强度自动采纳" not in text


def test_resume_await_approval_when_checklist_done(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text(
        _req_doc(checklist_resolved=True, approval="pending"), encoding="utf-8"
    )
    hint = resolve_phase_resume(tmp_path, "analyze_requirements", {})
    assert hint is not None
    assert hint.stage == "await_approval"
    assert hint.auto is False
    assert "立即进入定稿确认" in hint.inject_markdown
    assert "禁止再开澄清循环" in hint.inject_markdown
    assert "请用户确认" in hint.inject_markdown
    assert "禁止**自行把 approval" in hint.inject_markdown


def test_resume_await_approval_auto_direct_approve(tmp_path: Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    (req / "01-requirements.md").write_text(
        _req_doc(checklist_resolved=True, approval="pending"), encoding="utf-8"
    )
    hint = resolve_phase_resume(
        tmp_path, "analyze_requirements", {"auto": True}
    )
    assert hint is not None
    assert hint.stage == "await_approval"
    assert hint.auto is True
    text = hint.inject_markdown
    assert "立即定稿收尾" in text
    assert "直接写为 `approved`" in text
    assert "请用户确认" not in text
    assert "不要**再向用户展示确认菜单" in text or "不要再向用户展示确认菜单" in text


def test_insert_resume_after_run_mode_header() -> None:
    prompt = "\n".join(
        [
            "> **运行模式：无人值守（--auto）** — 禁止提问；approval 直接 approved。",
            "> **写作规范与人工介入版相同**：仅「问用户」改为「自行裁决」。",
            "",
            "## 路径约定（必须遵守）",
            "",
            "正文。",
            "",
        ]
    )
    inject = "> **断点续跑（引擎判定）**：立刻进澄清。\n\n"
    out = insert_resume_inject(prompt, inject)
    mode_idx = out.index("> **运行模式")
    resume_idx = out.index("> **断点续跑")
    chapter_idx = out.index("## 路径约定")
    assert mode_idx < resume_idx < chapter_idx
    assert out.index("写作规范与人工介入版相同") < resume_idx


def test_insert_resume_fallback_prepend_without_mode_header() -> None:
    prompt = "## 路径约定\n\n正文。\n"
    inject = "> **断点续跑（引擎判定）**：x\n\n"
    assert insert_resume_inject(prompt, inject).startswith(inject)


def test_resume_not_applied_to_implement(tmp_path: Path) -> None:
    assert resolve_phase_resume(tmp_path, "implement_code", {}) is None
