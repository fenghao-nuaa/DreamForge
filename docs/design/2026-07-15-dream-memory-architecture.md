# DREAM 记忆与做梦架构设计

**状态：** 已确认  
**日期：** 2026-07-15  
**参考实现：** Hermes Agent 0.18.2  
**部署方式：** 独立服务，后续通过适配器接入 AGFS-MEM

## 1. 目标

DREAM 将 Hermes 的 Background Review、`skill_manage` 和 Curator 机制移植为独立服务，并增加多租户路径解析和三类记忆产物。核心定义是：

> 将经历转化为决策能力，将交互转化为用户理解，将资料转化为技能、记忆和知识，并通过周期性做梦持续整理这些产物。

DREAM 不重新设计技能进化算法。Hermes 的后台复盘、限制工具、原子写入、技能生命周期、快照、报告和回滚仍是主要机制。

## 2. 已确认的方案

采用一个 Hermes Background Review 做分类并只调用必要管理工具，之后由三个 Curator 分别维护：

1. **Skill Curator**：维护 Agent Skills，尽量保留 Hermes 原始实现。
2. **AI Curator**：维护 AI 决策卡，并把重复验证的稳定原则合并到 `SOUL.md` 或 `DECISION_RULES.md`。
3. **User Curator**：维护当前用户的 `USER.md`、`MEMORY.md` 和 `TODOS.md`，处理重复、时序冲突、过期信息和已完成事项。

LLM Wiki 保持 Hermes 中的普通 Skill 身份。文件摄取是正常 Agent 任务，不能把任意文件工具加入 Background Review 的工具白名单。

第一交付优先级进一步收窄为 AI 决策卡和用户画像。上游短期记忆服务负责 Redis、Headroom 压缩和最近对话保存，通过 API 把已完成的对话批次推送给 DREAM；DREAM 不直接读取 Redis。Skill、文件知识和 LLM Wiki 在这条主链路稳定后继续实现。

## 3. 非目标

- 不在第一阶段修改 AGFS-MEM。
- 不重写 Hermes 的技能创建或技能 Curator 算法。
- 不让做梦写入影响正在运行的任务。
- 不在不同租户或不同用户之间共享用户画像、待办或证据。
- 不把所有声明性知识强制转成 Skill。
- 不让 Background Review 直接遍历任意上传目录或企业文件系统。

## 4. 总体数据流

```text
任务/对话/文件处理完成
        |
        v
不可变事件账本 + 原始来源引用
        |
        v
Hermes Background Review Fork
        |
        +-- AI 行为经验 --------> decision_card_manage
        +-- 用户事实/偏好 ------> memory_manage(target=user)
        +-- 用户待办 -----------> todo_manage
        +-- 环境/项目事实 ------> memory_manage(target=agent)
        +-- 程序性工作流 -------> skill_manage
        +-- 声明性知识 ----------> 不在后台直接写 Wiki；发出 wiki_ingest 建议事件
        |
        v
形成候选版本，不改变当前任务快照
        |
        v
Skill Curator / AI Curator / User Curator
        |
        v
快照、报告、归档、回滚
        |
        v
下一任务启动时解析并固定新版本
```

## 5. 作用域和磁盘布局

所有读写都必须先解析 `tenant_id`、`agent_id` 和 `user_id`。服务层不能接受调用方提交的绝对路径。

```text
$DREAM_HOME/
├── ledger/
│   └── events.jsonl
└── tenants/
    └── {tenant_id}/
        └── agents/
            └── {agent_id}/
                ├── SOUL.md
                ├── DECISION_RULES.md
                ├── decision-cards/
                ├── skills/
                ├── wiki/
                ├── users/
                │   └── {user_id}/
                │       ├── USER.md
                │       ├── MEMORY.md
                │       ├── TODOS.md
                │       └── evidence/
                ├── snapshots/
                ├── dream-reports/
                └── curator-state/
```

共享范围：

- `SOUL.md`、`DECISION_RULES.md`、`decision-cards/` 和 `skills/` 属于当前 `agent_id`。
- `USER.md`、用户 `MEMORY.md`、`TODOS.md` 和用户证据属于当前 `user_id`。
- Wiki 默认属于当前 `agent_id`，与 Hermes 的 `WIKI_PATH` 隔离方式一致；企业部署时由 scope resolver 为每个智能体设置独立路径。

## 6. 统一 Background Review

### 6.1 触发条件

保留 Hermes 的主要触发语义：只有任务得到正常最终响应且没有被中断，才累积并检查后台审查阈值。DREAM 另外接受独立服务需要的任务完成事件：

- 复杂任务完成事件进入队列；
- 达到累计工具迭代阈值时触发增量 Review；
- 空闲期处理等待队列；
- 周期任务运行三个 Curator。

后台失败只能记入报告，不能改变前台任务结果。

### 6.2 输入

Review 使用任务结束时的冻结输入：

- 当前任务消息历史或低成本摘要；
- 工具调用和结果；
- 最终响应；
- `tenant_id`、`agent_id`、`user_id`；
- 当前任务固定的记忆版本；
- 文件来源的哈希、路径别名和修改时间；
- 已加载技能及其使用记录。

### 6.3 输出分类

分类器不返回自由路径，只返回候选动作：

```python
ArtifactKind = Literal[
    "decision_card",
    "user_profile",
    "user_todo",
    "agent_memory",
    "skill",
    "wiki_ingest",
    "nothing",
]
```

每个动作必须包含来源事件 ID。`wiki_ingest` 只创建建议事件，由正常 Agent 任务显式加载 llm-wiki Skill 后执行。

### 6.4 工具最小化

Review fork 根据本次分类结果只暴露需要的管理工具，而不是永久携带所有工具：

- `decision_card_manage`
- `memory_manage`
- `todo_manage`
- `skill_manage`

Review fork 禁止终端、浏览器、网络、任意文件读取和 llm-wiki。管理工具内部再次执行 scope 校验、内容扫描、原子写入和来源记录。

## 7. AI 提纯

### 7.1 决策卡

决策卡是磁盘可验证的 Markdown 文件，记录场景、信号、选择原则、结果、边界和证据。

```yaml
---
id: decision-20260715-001
status: active
confidence: 0.82
created_at: 2026-07-15T09:00:00+08:00
updated_at: 2026-07-15T09:00:00+08:00
source_event_ids:
  - event-123
tags:
  - clarification
  - reversible-action
---
```

正文必须包含：使用场景、决策信号、决策原则、本次结果、反例与边界。

### 7.2 AI Curator

AI Curator 沿用 Hermes Curator 的后台运行、快照、报告、归档和回滚结构，并使用决策卡专用审查规则：

- 合并同义或高度重叠的卡片；
- 保留来源和合并前版本；
- 标记互相冲突的决策原则；
- 归档长期无证据支持或已被替代的卡片；
- 将多次独立证据支持的稳定原则写入 `DECISION_RULES.md`；
- 只有身份层面的稳定变化才更新 `SOUL.md`。

自动更新仍必须先创建快照，并在报告中写明证据和变更。更新只对下一任务生效。

## 8. 用户提纯

### 8.1 用户画像

`USER.md` 保存相对稳定的用户事实、偏好、沟通方式、目标和约束。每条信息附带：

- 首次发现时间；
- 最近确认时间；
- 置信度；
- 来源事件 ID；
- 当前状态：active、contested、stale 或 archived。

### 8.2 待办

`TODOS.md` 独立于画像，至少包含 ID、内容、状态、来源、创建时间、更新时间和可选截止时间。状态只允许 `pending`、`in_progress`、`completed`、`cancelled`、`expired`。

### 8.3 User Curator

User Curator 一次只能挂载一个 `tenant_id/agent_id/user_id` 作用域：

- 合并重复画像；
- 用时间证据处理“过去是 A、现在是 B”；
- 无法判断时同时保留并标记 contested；
- 归档已失效画像，而不是无痕删除；
- 迁移完成、取消或过期待办；
- 不把一个用户的偏好提升为全局 AI 身份。

## 9. 文件和聊天知识提炼

文件来源仅允许显式上传和配置中的白名单目录。原始内容不由 Background Review 直接读取。

1. 正常 Agent 任务读取文件或聊天记录。
2. 保存来源哈希、逻辑路径、修改时间和任务 ID。
3. Agent 可在正常任务中加载 llm-wiki Skill，维护当前智能体的 Wiki。
4. 任务完成事件进入统一 Background Review。
5. Review 把程序性知识写成 Skill，把用户事实写入画像，把待办写入 `TODOS.md`，把环境事实写入 `MEMORY.md`，把 AI 行为经验写成决策卡。
6. 文件变化时产生新的来源事件；派生产物标记为需要复核，不直接删除。

知识产物边界：

- Wiki：我们知道什么，以及实体和概念之间的关系。
- Skill：遇到这种任务时具体如何执行。
- Memory：当前智能体或用户以后必须记住什么。
- Decision Card：智能体在某类情境下如何选择。

## 10. 快照、版本和下一任务生效

每个前台任务启动时创建 `ContextSnapshot`，固定：

- `SOUL.md` 和决策规则版本；
- 用户画像、记忆和待办版本；
- 技能索引版本；
- Wiki 路径和索引版本。

后台写入产生新候选版本，但不能修改正在运行任务的 `ContextSnapshot`。下一任务启动时才解析最新有效版本。

每次 Curator 运行必须产生：

- 运行前快照；
- 动作清单；
- 来源证据；
- 变更摘要；
- 失败信息；
- 可回滚快照 ID。

## 11. 安全和隔离不变量

1. 任意管理工具都不能接受绝对路径或 `..`。
2. `tenant_id`、`agent_id`、`user_id` 经过严格字符白名单校验。
3. User Curator 不能读取兄弟用户目录。
4. Review fork 不具有网络、终端和任意文件工具。
5. 原始来源不可被 Curator 修改。
6. 自动修改前必须有快照，自动删除改为归档。
7. 当前任务上下文在生命周期内保持字节稳定。
8. 所有派生产物保留来源事件 ID。
9. Hermes 复制代码保留 MIT License 和 Nous Research 版权声明。

## 12. 验收标准

- 同一对话可分别产生决策卡、用户画像和 Skill，且不会交叉写错位置。
- 两个用户使用同一 AI 时共享 AI 身份和技能，但互相看不到画像、记忆和待办。
- 后台 Review 写入后，当前任务仍使用旧快照，下一任务使用新快照。
- 文件知识能被正常 Agent 任务分流到 Wiki、Skill、Memory 或 TODO。
- 三个 Curator 都能生成报告、创建快照、归档产物并回滚。
- Background Review 或 Curator 失败不会使前台任务失败。
- 可以从磁盘文件和事件账本验证一次学习的来源、结果和生效版本。

## 13. 分阶段交付

1. **基础闭环**：作用域、事件账本、任务完成入口、Background Review 适配、冻结快照、报告和回滚。
2. **AI 提纯**：决策卡管理工具和 AI Curator。
3. **用户提纯**：多用户 `USER.md`、`MEMORY.md`、`TODOS.md` 和 User Curator。
4. **知识提炼**：白名单文件摄取、Skill 分流和普通 llm-wiki Skill 接入。
5. **AGFS-MEM 适配**：保持磁盘为可审计来源，通过接口同步或检索，不改变 DREAM 的核心产物格式。
