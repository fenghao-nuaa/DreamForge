# Dreams README Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing README with a Chinese, self-contained description of Dreams that centers user-persona refinement and anthropomorphic AI decision evolution.

**Architecture:** This is a documentation-only change. The README will follow the section rhythm of AGFS-MEM while using the synthesis narrative approved in the design: completed agent Sessions and existing artifacts are reviewed, split into two refinement paths, periodically consolidated, and exposed through a new snapshot on the next task.

**Tech Stack:** Markdown documentation; existing Python/FastAPI project interfaces are documented but not changed.

## Global Constraints

- The project name in the README is `Dreams`.
- The README must not mention or compare any other concrete project.
- The README must not claim Redis, mem0, Embedding, or vector-database usage.
- The user output is a continuously evolving `USER.md`, not a generated callable Skill.
- The AI output is Markdown decision cards plus `DECISION_RULES.md`.
- “Anthropomorphic” means a stable, continuous, traceable decision identity, not tone imitation or role-play.
- Session input may contain `user`, `assistant`, `system`, and `tool` messages.
- Dreams preserves mutation snapshots and reports; it must not claim copy-on-write output stores.

---

### Task 1: Rewrite and verify README

**Files:**
- Modify: `README.md`
- Reference: `docs/superpowers/specs/2026-07-16-readme-rewrite-design.md`
- Reference: `src/dream/api.py`
- Reference: `src/dream/service.py`
- Reference: `src/dream/source_sync.py`

**Interfaces:**
- Consumes: existing HTTP routes and environment variables exactly as implemented.
- Produces: one self-contained project README; no runtime interface changes.

- [ ] **Step 1: Replace the opening and architecture**

Use this approved opening:

```markdown
# Dreams

Dreams 让 AI 智能体在两次任务之间回顾过去的会话与任务经历，持续提纯用户人物画像，并塑造自身的决策身份。

智能体在工作过程中积累的信息通常是局部和增量的。随着 Session 不断增加，用户信息会出现重复、变化和矛盾，AI 的决策经验也会散落在不同任务中，难以在未来直接复用。

核心思路：**智能体过去的会话与任务经历通过 LLM 回顾，分别提纯为持续进化的 `USER.md` 用户人物画像和 AI 决策卡**；**两类产物再通过周期做梦合并重复信息、修正过时或冲突内容、归档旧经验，并从下一次任务开始生效。**
```

Follow it with an ASCII architecture showing:

```text
completed Session + existing artifacts
                 ↓
        background review/classification
             ↙                 ↘
 USER.md persona refinement   AI decision cards
             ↘                 ↙
       periodic dual curation
                 ↓
       next-task context snapshot
```

- [ ] **Step 2: Add the AGFS-MEM-aligned sections**

Use these exact section titles and document only existing behavior:

```markdown
## 架构
## 核心产物
## 项目结构
## API
## 用户人物画像提纯
## AI 决策身份提纯
## 周期做梦
## Session 接入
## 多用户服务
## 快照、报告与回滚
## 容错策略
## 核心模块
## 配置
## 开发
```

The user-persona section must cover identity, preferences, communication habits, work style, goals, constraints, interaction expectations, evidence preservation, and `USER.md` output. The AI section must cover scenario, signals, principle, outcome, boundaries, confidence, card consolidation, archived overlaps, and `DECISION_RULES.md`.

- [ ] **Step 3: Document actual routes and inputs**

List the implemented routes:

```text
POST /v1/dream/conversations
POST /v1/tasks/start
POST /v1/dream/run-pending
POST /v1/dream/run-curators
POST /v1/dream/run-due-curators
POST /v1/dream/rollback/{snapshot_id}
GET  /v1/dream/reports/{run_id}
```

Describe push ingestion and optional NDJSON/cursor pull without naming another project. State that a source export record contains `cursor`, `event_id`, `user_id`, `session_id`, `round_id`, `completed_at`, `messages`, and `final_response`.

- [ ] **Step 4: Verify documentation constraints**

Run:

```bash
rg -n "Hermes|0\.18\.2|Internship|Redis|mem0|Embedding|Elasticsearch" README.md
```

Expected: no matches.

Run:

```bash
rg -n "^# Dreams$|USER\.md|DECISION_RULES\.md|AI 决策卡|下一次任务" README.md
```

Expected: matches for the project name, both artifact types, AI decision cards, and next-task activation.

- [ ] **Step 5: Run project verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/fenghao/PycharmProjects/dream/.venv/bin/python -m pytest -p no:cacheprovider -q
/Users/fenghao/PycharmProjects/dream/.venv/bin/ruff check --no-cache src tests
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, and `git diff --check` prints nothing.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/plans/2026-07-16-readme-rewrite.md
git commit -m "docs: rewrite Dreams project overview"
```
