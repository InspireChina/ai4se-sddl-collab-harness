---
name: reopen-workflow
description: >-
  收集验收前变更点并写入需求目录的 change 文件，再拼装 sddl reopen 命令。
  在用户说需求变更、方案变更、实现变更、中途改需求、reopen、reopen-workflow 时使用。
  本 skill 只写 change 文件并准备命令，不在当前 AI session 内执行 sddl reopen。
---

# Reopen Workflow

根据用户口述/粘贴的变更点，在已有需求目录写入 `changes/change-vN.md`，再交出 `sddl reopen` 命令（在 AI session **外**执行）。不要改引擎实现。

## 硬性约束

1. **禁止**在当前 AI session 里用 Shell 执行 `sddl reopen` / `sddl start` / `start.sh`。
2. 准备完成后：展示 change 文件路径与 reopen 命令，说明在外部终端执行，然后停止。
3. 命令用空格形式：`sddl reopen`，不要 `sddl:reopen`。

## 1. 锁定需求目录（`--name`）

1. 从用户输入或对话推断卡号/需求名。
2. `project_docs_dir` 以业务根 `sdd-loop-workflow-config/workflow-local.yaml` 为准；配置为空或目录不存在 → 说明后停止。
3. **必须与已有 spec 对上**：检查 `<project_docs_dir>/spec/` 下是否存在与该名称对应的目录（通常即 `spec/<name>/`）。
   - 对得上 → 用该目录名作为 `--name`。
   - 对不上（目录不存在、或名字对不上任何 `spec/*`）→ **停下来问用户确认**（是否写错、是否要用已有哪个目录、是否尚未 start）。**禁止**猜一个新名继续写 change / 拼命令。
4. 目标目录须已有首轮产出（至少 `requirements/01-requirements.md`）；没有则提示先 `sddl start`，停止。

## 2. 收集变更点

本步**只收集「改什么」的白话描述**。此时还不知道会改到哪份文档、哪个章节，**不要**编造落点、锚点、Story/Task/用例编号。

1. **对话里已经说清变更内容** → 直接整理成条目，向用户复述确认后写入（不必再空问一遍「要改什么」）。
2. **对话里没有** → 再问用户：这次要改什么（可多条，用白话）。
3. 不要问「落在需求还是计划」「对应哪个 US/T」之类——那是后续 workflow 节点对照旧产出时才判断的事。

## 3. 写入 change 文件

目录：`<project_docs_dir>/spec/<name>/changes/`（没有就创建）。

版本：已有 `change-v1.md`、`change-v2.md`… 则下一版为最大序号 +1；没有则从 `v1` 起。

文件名：`change-vN.md`。

**只描述变更点，不要整份重写需求，也不要臆造定位字段。** 模板：

```markdown
# 变更 change-vN

```yaml change_metadata
run_mode: reopen
change_version: vN
change_scope: "一句话概括本次变更"
change_details:
  - "用白话写清要改什么（一条一条列）"
  - "另一条变更点"
```

```yaml change_status
status: pending
last_node: ""
all_docs_updated: false
updated_at: ""
note: ""
```

## 变更说明

（可选：再写几句背景，给后续节点读。仍然只写「改什么」，不写技术落点猜测。）
```

说明：

- `change_details` 用**字符串列表**即可；不要写 `id: C1` / `locate_hint` / `anchor`（本 skill 阶段不知道这些）。
- `change_status` 初始写 `pending`；引擎会更新 `status` / `last_node`（**仅记录**，续跑不靠它）。
- 续跑看各节点 md 的 `reopen_impact.reviewed_changes`（仅 `changes/change-vN.md`）。再次 `sddl reopen` 时从尚未列入本 change 的最早节点接着跑。
- reopen 临时提示词写在 `temp-prompts/change-vN/`，不覆盖首轮 `temp-prompts/`。
- 若最新 change 已是 `completed`，本次应新建下一版 `change-v(N+1).md`，不要复用已完成文件。
- 写入后把该文件的**绝对路径**告诉用户。

## 4. 拼装 reopen 命令

在业务项目根执行。推荐：

```bash
sddl reopen --name "<name>"
```

若用户明确要无人值守/别问我/自动跑完，追加 `--auto`。

可再给一条调试用（仅当业务未配 tool 或用户要求强制本环境工具）：

```bash
sddl reopen --tool cursor --name "<name>"
```

说明：

- `sddl reopen` 内部转调 `start.sh --reopen`（Windows：`start.cmd --reopen`），与 `sddl start` 共用 venv/依赖检查。
- **尚无节点复核过本 change** 时引擎会把已有阶段 md 的 approval 打回 `pending` 并从需求分析起对照；中途停掉后再 `sddl reopen` 则按各节点 `reopen_impact.reviewed_changes` 续跑。有差异则**直接覆盖该节点原有产出文件**；无差异则 `impact: none` 快过。
- 请复制命令到**新终端 / 退出当前 AI session 后**执行。
