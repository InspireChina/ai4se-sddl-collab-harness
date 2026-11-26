"""解析需求分析节点产出的结构化元数据。"""

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

REQUIREMENT_TYPE_ALIASES = {
    "new": "new",
    "新需求": "new",
    "existing_change": "existing_change",
    "existing_baseline": "existing_change",  # 固化现有行为 / 验收基线
    "baseline": "existing_change",
    "老需求调整": "existing_change",
    "老需求固化": "existing_change",
    "存量需求调整": "existing_change",
    "需求调整": "existing_change",
    "固化": "existing_change",
}

RISK_ALIASES = {
    "low": "low",
    "低": "low",
    "medium": "medium",
    "中": "medium",
    "high": "high",
    "高": "high",
}

RUN_MODE_ALIASES = {
    "initial": "initial",
    "first": "initial",
    "首次": "initial",
    "首轮": "initial",
    "reopen": "reopen",
    "change": "reopen",
    "变更": "reopen",
    "需求变更": "reopen",
}


@dataclass
class RequirementMetadata:
    requirement_type: str = "unknown"
    requirement_summary: str = ""
    change_scope: str = ""
    affected_modules: list[str] = field(default_factory=list)
    compatibility_risk: str = "unknown"
    needs_clarification: bool = False
    open_questions_count: int = 0
    judgment_basis: str = ""
    # initial = 首轮交付；reopen = 验收前变更轮次（与 requirement_type 正交）
    run_mode: str = "initial"
    change_version: str = ""
    change_details: list[dict[str, str]] = field(default_factory=list)

    def to_state_updates(self) -> dict[str, Any]:
        return {
            "requirement_type": self.requirement_type,
            "requirement_summary": self.requirement_summary,
            "change_scope": self.change_scope,
            "affected_modules": self.affected_modules,
            "compatibility_risk": self.compatibility_risk,
            "run_mode": self.run_mode,
            "change_version": self.change_version,
            "change_details": list(self.change_details),
            "requirement_metadata": {
                "needs_clarification": self.needs_clarification,
                "open_questions_count": self.open_questions_count,
                "judgment_basis": self.judgment_basis,
                "run_mode": self.run_mode,
                "change_version": self.change_version,
                "change_details": list(self.change_details),
            },
        }

    def to_context(self) -> dict[str, str]:
        modules = (
            "\n".join(f"- {m}" for m in self.affected_modules)
            if self.affected_modules
            else "（无）"
        )
        type_label = "新需求" if self.requirement_type == "new" else "老需求调整"
        if self.requirement_type == "unknown":
            type_label = "未识别"
        run_label = "验收前变更" if self.run_mode == "reopen" else "首轮交付"
        if self.change_details:
            detail_lines = []
            for item in self.change_details:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "").strip() or "?"
                summary = str(item.get("summary") or "").strip() or "（无摘要）"
                line = f"- **{cid}**: {summary}"
                hint = str(item.get("locate_hint") or "").strip()
                anchor = str(item.get("anchor") or "").strip()
                if hint:
                    line += f"\n  - 落点提示: `{hint}`"
                if anchor:
                    line += f"\n  - 锚点: `{anchor}`"
                detail_lines.append(line)
            change_details_text = "\n".join(detail_lines) if detail_lines else "（无）"
        else:
            change_details_text = "（无）"

        return {
            "requirement_type": self.requirement_type,
            "requirement_type_label": type_label,
            "requirement_summary": self.requirement_summary,
            "change_scope": self.change_scope,
            "affected_modules": modules,
            "compatibility_risk": self.compatibility_risk,
            "needs_clarification": str(self.needs_clarification).lower(),
            "open_questions_count": str(self.open_questions_count),
            "judgment_basis": self.judgment_basis,
            "run_mode": self.run_mode,
            "run_mode_label": run_label,
            "change_version": self.change_version or "（无）",
            "change_details": change_details_text,
            "change_details_text": change_details_text,
        }


def parse_requirement_metadata(content: str) -> RequirementMetadata:
    raw = _extract_yaml_block(content)
    return _from_yaml(raw) if raw else _from_markdown_fallback(content)


def _extract_yaml_block(content: str) -> dict[str, Any] | None:
    patterns = [
        r"```yaml\s+requirement_metadata\s*\n(.*?)```",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        try:
            data = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _normalize_type(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = REQUIREMENT_TYPE_ALIASES.get(str(value).strip(), "unknown")
    return normalized


def _normalize_run_mode(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return "initial"
    key = str(value).strip().lower()
    return RUN_MODE_ALIASES.get(key, RUN_MODE_ALIASES.get(str(value).strip(), "initial"))


def _normalize_risk(value: Any) -> str:
    if value is None:
        return "unknown"
    return RISK_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip("- ").strip() for line in value.splitlines() if line.strip()]
    return [str(value)]


def _as_change_details(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [{"id": "C1", "summary": text, "locate_hint": "", "anchor": ""}] if text else []
    if not isinstance(value, list):
        return []
    details: list[dict[str, str]] = []
    for idx, item in enumerate(value, start=1):
        if isinstance(item, str):
            details.append(
                {
                    "id": f"C{idx}",
                    "summary": item.strip(),
                    "locate_hint": "",
                    "anchor": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        details.append(
            {
                "id": str(item.get("id") or f"C{idx}").strip(),
                "summary": str(
                    item.get("summary") or item.get("detail") or item.get("desc") or ""
                ).strip(),
                "locate_hint": str(
                    item.get("locate_hint")
                    or item.get("impact_hint")
                    or item.get("locate")
                    or ""
                ).strip(),
                "anchor": str(item.get("anchor") or item.get("location") or "").strip(),
            }
        )
    return details


def _from_yaml(data: dict[str, Any]) -> RequirementMetadata:
    if "requirement_metadata" in data and isinstance(data["requirement_metadata"], dict):
        data = data["requirement_metadata"]

    change_version = str(data.get("change_version") or "").strip().lower()
    if change_version and not change_version.startswith("v"):
        change_version = f"v{change_version}"

    return RequirementMetadata(
        requirement_type=_normalize_type(data.get("requirement_type")),
        requirement_summary=str(data.get("requirement_summary", "")).strip(),
        change_scope=str(data.get("change_scope", "")).strip(),
        affected_modules=_as_str_list(data.get("affected_modules")),
        compatibility_risk=_normalize_risk(data.get("compatibility_risk")),
        needs_clarification=bool(data.get("needs_clarification", False)),
        open_questions_count=int(data.get("open_questions_count", 0) or 0),
        judgment_basis=str(data.get("judgment_basis", "")).strip(),
        run_mode=_normalize_run_mode(data.get("run_mode")),
        change_version=change_version,
        change_details=_as_change_details(data.get("change_details")),
    )


def _from_markdown_fallback(content: str) -> RequirementMetadata:
    metadata = RequirementMetadata()

    type_match = re.search(
        r"需求类型\s*[:：]\s*`?(新需求|老需求调整|存量需求调整)`?",
        content,
    )
    if type_match:
        metadata.requirement_type = _normalize_type(type_match.group(1))

    summary_match = re.search(r"需求摘要\s*[:：]\s*(.+)", content)
    if summary_match:
        metadata.requirement_summary = summary_match.group(1).strip()

    basis_match = re.search(r"判断依据\s*[:：]\s*(.+)", content)
    if basis_match:
        metadata.judgment_basis = basis_match.group(1).strip()

    risk_match = re.search(
        r"兼容(?:性)?风险\s*[:：]\s*`?(low|medium|high|低|中|高)`?",
        content,
        re.IGNORECASE,
    )
    if risk_match:
        metadata.compatibility_risk = _normalize_risk(risk_match.group(1))

    return metadata
