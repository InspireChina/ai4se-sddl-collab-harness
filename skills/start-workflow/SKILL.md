---
name: start-workflow
description: >-
  准备并拼装 SDD workflow 启动命令（需求到代码再到测试）。在用户要求启动
  workflow、开始实现需求、无人值守/全自动跑完，或给出本地 PRD 路径、Colla、
  Jira 等需求来源时使用。本 skill 只准备命令，不在当前 AI session 内执行启动；
  根据上下文自动拼接全部相关参数（含 --auto / --name / --fresh 等）。
---

# 启动 Workflow

把需求落到一个本地文件，**根据上下文自动拼齐启动参数**，把完整命令交给用户在 AI session 外执行。
不要改 workflow 实现。不要等用户逐个点名参数——从对话意图里推断。

## 硬性约束（必须遵守）

1. **禁止**在当前 AI session 里用 Shell / 终端工具执行 `sddl start`（或 `start.sh` / `start.cmd`）。
2. **禁止**为「续跑」「审批后继续」再次拼装/执行启动命令；审批通过后由**已在外部终端运行的** Python 进程自动续跑。
3. 准备完成后：展示命令并说明在外部终端执行，然后停止（不要调用执行类工具）。
4. **命令必须用空格形式** `sddl start` / `sddl init`（或 `sddl-start` / `sddl-init`），**不要**拼 `sddl:start`。

## 1. 确定 PRD 文件（得到 `--file`）

先看上下文有没有本地 PRD 路径；再看卡号对应目录是否已经存在；再看用户是否**直接给了一个 URL**；都没有才按卡号去 Colla/Jira 读。

### 正文格式（**必须 Markdown**）

`--file` 指向的需求正文**必须是 Markdown**（`.md`）。若来源是其它格式，**先转成 Markdown 再落盘**，再用转换后的路径作为 `--file`：

- 常见来源：HTML / 富文本、Confluence/飞书/语雀导出、Word（`.docx`）、纯文本、JSON/XML 字段拼出的说明、Jira/Colla 工单描述与评论等
- 转换要求：保留标题层级、列表、表格、链接、代码块；去掉无意义的脚本/样式/导航壳；不臆造原文没有的需求
- 落盘位置：`<project_docs_dir>/origin_requirements/<标识>.md`（目录不存在就创建；同名覆盖）
- 已是结构清晰的 `.md` → 可直接用作 `--file`，不必再拷一份
- 图片/附件统一：`<project_docs_dir>/origin_requirements/assets/<标识>/`；md 内用相对路径，如 `![说明](assets/<标识>/ui-1.png)`

### 用户直接给了一个链接（**要抓正文，仅一层**）

当用户把 **URL 本身**当作需求来源（例如直接发一篇文档/页面链接，而不是「去 Colla 拉某某卡」）时：

1. 打开该 URL，把**这一页**的正文抓取并转成 Markdown，写入 `origin_requirements/<标识>.md`（页顶可保留来源 URL）。
2. 这一页上的**图片、附件**下载到 `assets/<标识>/`，md 改相对路径引用。
3. 这一页里若**再嵌套其它链接**：**只原样留在 md 里，不要继续打开/抓取**（仅一层）。嵌套链接的详情留给需求分析节点按需阅读。
4. 打开方式见下方「打开需登录页面」。抓不到 → 说明原因并停止，不要拼命令。

### Colla / Jira 按卡号读取（**必须读全**）

从 Colla、Jira（或同类工单）按卡号/工单读时，**必须把该需求相关信息读全**，再整理成一份 Markdown。禁止只拉标题/描述就落盘。

**至少覆盖（有则必读，不限于此）**：

| 类别 | 要求 |
|------|------|
| 标题、描述、验收/字段自定义项 | 全部写入 md |
| **评论**（含讨论、决策、补充说明） | 按时间或线程完整收录，不要摘要掉关键结论 |
| **附件** | 全部下载到 `assets/<标识>/`，md 内相对路径引用；列清单防遗漏 |
| **图片**（描述/评论内嵌） | 同上，落到 `assets/<标识>/` |
| **子任务** | 逐个打开读取（标题、描述、状态、评论要点等），写入 md（可分节） |
| **关联任务 / 链接工作项**（阻塞、相关、复制自等） | 逐个打开读取关键正文与结论，写入 md；注明关联类型与卡号 |
| 史诗/父任务、验收标准、标签、组件等元数据 | 有则写入，便于后续分析 |

落盘时建议在 md 里分节，例如：`## 主单`、`## 评论`、`## 子任务`、`## 关联任务`、`## 附件`。

**与「外链仅一层」的边界**：

- 工单体系内的子任务、关联任务 → **要跟进去读全**（仍属 Colla/Jira 读全范围）。
- 描述/评论里指向**站外**的文档、设计稿、原型等 URL → **原样保留在 md**，本 skill 不展开；由需求分析节点按需打开。

读不全（权限、MCP 缺字段、附件下载失败）→ 在 md 里明确列出「未读到的项 + 原因」，不要假装已读全；关键附件失败时问用户或停止，不要默默省略。

### 打开需登录页面

用于：用户直接给的那一个 URL，或 Colla/Jira **工单页本身**。按你**当前所在 Agent** 选一行；不要套用其它 Agent；不要默认无痕 headless（会丢 SSO）。

| 当前 Agent | 打开与登录读法 |
|------------|----------------|
| **omp** | 自带 Browser / `xd://browser`；遇登录墙时**模拟点击**「使用此账号登录」等，沿用当前已登录账号后再读页。 |
| **Cursor** | Cursor 内置 **Browser**；在其中登录（可按 workspace 持久化）后读取。 |
| **Claude Code** | 已配置的浏览器 / Browser MCP；未配置则问用户，不要猜 MCP 名。 |
| **Codex** | 已配置的浏览器 MCP / 官方读法；未配置则问用户。 |

Colla/Jira 优先：项目已有 **MCP/API** → 再才用浏览器打开工单页。

### 已给出本地路径

1. 解析成绝对路径，确认文件存在。
2. 不要再访问 Colla、Jira 等。
3. 若已是 Markdown → 该绝对路径即为 `--file`。
4. 若是其它格式 → 按上一节转成 Markdown，写入 `origin_requirements/<标识>.md`，**以该 `.md` 路径**为 `--file`（不要把 `.docx`/`.html` 等直接传给 `sddl start`）。

路径不存在或是目录 → 说明情况，停止，不要拼命令。

### 卡号对应目录已存在

用户给的是卡号（需求编号，如 Colla、Jira）时，先看 `<project_docs_dir>/spec/<卡号>/`。`project_docs_dir` 以业务根 `sdd-loop-workflow-config/workflow-local.yaml` 为准。

目录已经存在：不要调用 MCP，不要再写 `origin_requirements` 文件。用该卡号作为 `--name` 直接拼启动命令。目录里已有 `00-requirement.md` 时不要加 `--file`。

目录不存在：再走下面的外部读取。

### 用户直接给了 URL

按「用户直接给了一个链接」抓取**仅一层**后，写入 `origin_requirements/<标识>.md`，该路径作为 `--file`。

### 要从 Colla、Jira 或其它外部系统按卡号读取

1. MCP 与读法只以本次 skill 输入和项目已有 rule 为准（Cursor rules、`CLAUDE.md`、`AGENTS.md`、omp rules、`ai-rules` 等）。
2. 不要猜 MCP 名，不要自编读法；没说明就问用户，不要拼命令。
3. 按「Colla / Jira 按卡号读取」**读全**后转 Markdown：评论、附件、子任务、关联任务等均纳入；站外 URL 仅保留链接；图片与附件下到 `assets/<标识>/`。
4. 写入 `<project_docs_dir>/origin_requirements/<标识>.md`。`project_docs_dir` 以业务根 `sdd-loop-workflow-config/workflow-local.yaml` 为准。为空或目录不存在时不要写文件，说明配置错误后停止。`origin_requirements` 目录不存在就创建；同名覆盖。
5. 标识优先需求编号，否则用标题（只留字母、数字、中文、`-`）。
6. 主单正文与评论皆空且无子任务/关联 → 停止。写入后的绝对路径即为 `--file`。

### 都没有

问用户要本地 PRD 路径、直接可用的 URL，或 Colla/Jira 卡号。不要拼命令。

## 2. 读取业务 tool 配置

打开业务根 `sdd-loop-workflow-config/workflow-local.yaml`，检查是否配置了 AI 工具：

- 存在 `defaults.tool`，或任一 `nodes.<id>.tool` → **已配置**
- 否则 → **未配置**

（`model` 可选；编排引擎会处理降级，skill 拼命令时一般不必带 `--model`。）

## 3. 按上下文自动拼接参数

在业务项目根执行（cwd = `app_root`）。

**推荐基底（已配置 tool 时）——不带 `--tool`：**

```bash
sddl start --file "<PRD 绝对路径>"
```

**强制本环境工具基底（调试 / 未配置 / 用户要求）——带 `--tool`：**

```bash
sddl start --tool {tool} --file "<PRD 绝对路径>"
```

再**扫一遍对话/skill 输入**，按下面规则追加参数（能推断就加，不要逐项问用户「要不要加 xxx」）：

| 参数 | 何时加入 |
|------|----------|
| `--tool {tool}` | **仅当**走「强制本环境」命令时加入。值默认 `{tool}`；用户明确要求别的工具（如 `echo`）时才改。已配业务 `defaults.tool` 且用户未要求强制时，**推荐命令不要加** `--tool`。 |
| `--file <绝对路径>` | 上一步得到了本地 PRD 路径时加入。卡号对应的 `spec/<卡号>/` 已存在且里面已有 `00-requirement.md` 时**不要**加，也不要为此去拉 MCP。 |
| `--name <目录名>` | 用户指定了产出目录名/需求名/slug；或从 PRD 文件名、标题、需求编号能稳定推导出短标识时**主动加上**（便于续跑）。不要用超长标题。 |
| `--docs-dir <路径>` | 用户要覆盖默认产出目录，或明确说产出不在默认 `docs_dir`（如 `spec`）下时加上。未提及则不加。 |
| `--model <名>` | 用户明确指定模型时加上（会覆盖配置中全部节点的 model）。未提及不加。 |
| `--auto` | 出现无人值守意图：**无人值守、全自动、不打断、不提问、自动跑完、别问我、auto、unattended** 等。普通「启动一下」**不要**加。 |
| `--fresh` | 用户明确要重来、清空重跑、从头开始时加上。续跑时**禁止**加。 |
| `-h` / `--help` | 用户只想看帮助时，只输出帮助命令，不要拼启动。 |

规则补充：

1. **推断优先于追问**：上下文够用就直接拼；只有缺 PRD 路径/外部读法时才停下来问。
2. **互斥**：`--fresh` 与「续跑」互斥。
3. **不要**发明上表以外的参数。
4. 拼完后用一两句说明加了哪些开关。

## 4. 交出命令（到此结束，不要执行）

### 业务已配置 tool（`defaults.tool` 或节点 tool）

展示 **两条**命令，并写清区别；**推荐用 A**：

```bash
# A. 推荐：不带 --tool — 按 sdd-loop-workflow-config 的 tool/model（可按节点不同）
sddl start --file "/absolute/path/to/prd.md" --name login

# B. 强制本环境工具：覆盖配置里所有节点的 tool（调试或临时换工具）
sddl start --tool {tool} --file "/absolute/path/to/prd.md" --name login
```

说明：

- **A**：完全按业务 `workflow-local.yaml` 合并后的配置跑；可不改启动命令切换节点工具。
- **B**：CLI `--tool` 会**覆盖全部节点**，忽略配置里的按节点 tool。

### 业务未配置 tool

只给带 `--tool` 的命令（当前 agent = `{tool}`），并提示可在 `sdd-loop-workflow-config/workflow-local.yaml` 增加 `defaults.tool` 后改用不带 `--tool` 的启动方式。

```bash
sddl start --tool {tool} --file "/absolute/path/to/prd.md" --name login
```

收尾说明（两种情况都要）：

> 请复制命令，任选一种方式执行（不要在当前 AI 对话里跑）：
>
> 1. **新开一个终端**，`cd` 到业务项目根后粘贴执行；或
> 2. **退出 / 结束当前 AI session** 后，在系统终端里执行。
>
> Workflow 必须在 AI session 外跑，编排进程才能接管节点并自动续跑。
