# DREAM Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可独立部署的 DREAM 基础闭环，完成多租户作用域、任务事件账本、Hermes 风格 Background Review 调度、冻结快照、报告和回滚，并用简单磁盘记忆动作验证“本次写入、下次任务生效”。

**Architecture:** FastAPI 只负责接收任务完成和启动请求；核心服务把事件写入 JSONL 账本，再由单个 Background Review orchestrator 生成受限管理动作。磁盘产物通过作用域解析器和原子文件存储写入，任务启动时固定 manifest 版本，后台变化不修改当前快照。Hermes 代码移植放在 `hermes_compat` 中并保留 MIT 声明，业务适配代码不修改参考仓库。

**Tech Stack:** Python 3.11-3.13、FastAPI、Pydantic 2、Uvicorn、PyYAML、pytest、pytest-asyncio、标准库 `pathlib`/`json`/`threading`/`hashlib`。

## Global Constraints

- 项目目录固定为 `/Users/fenghao/PycharmProjects/dream/DREAM`。
- 第一阶段不修改 `/Users/fenghao/PycharmProjects/dream/AGFS-MEM`。
- 参考 Hermes Agent 0.18.2，复制代码必须保留 MIT License 和 `Copyright (c) 2025 Nous Research`。
- Background Review 只在任务正常完成且未中断时排队。
- Background Review 不暴露终端、网络、浏览器、任意文件读取或 llm-wiki 工具。
- 所有读写必须携带并校验 `tenant_id`、`agent_id` 和 `user_id`。
- 后台写入只对下一任务生效。
- 自动修改前创建快照，删除语义实现为可恢复归档。
- 第一阶段使用简单、确定性的 ReviewBackend 验证闭环；接入真实 LLM 时保持同一接口和工具白名单。

---

### Task 1: Scaffold the standalone service and preserve Hermes attribution

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `LICENSE`
- Create: `NOTICE`
- Create: `src/dream/__init__.py`
- Create: `tests/test_package.py`

**Interfaces:**
- Consumes: none.
- Produces: importable `dream` package with `__version__: str`.

- [ ] **Step 1: Write the failing package test**

```python
from dream import __version__


def test_package_version_is_explicit() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: Run the test and verify collection fails**

Run: `python -m pytest tests/test_package.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'dream'`.

- [ ] **Step 3: Create the package metadata**

```toml
[build-system]
requires = ["setuptools>=77.0,<83"]
build-backend = "setuptools.build_meta"

[project]
name = "dream-memory-service"
version = "0.1.0"
description = "Hermes-compatible background memory refinement service"
requires-python = ">=3.11,<3.14"
license = "MIT"
dependencies = [
  "fastapi>=0.104.0,<1",
  "pydantic>=2.13,<3",
  "pyyaml>=6.0,<7",
  "uvicorn[standard]>=0.24.0,<1",
]

[project.optional-dependencies]
dev = ["pytest>=9,<10", "pytest-asyncio>=1.3,<2", "httpx>=0.28,<1"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `src/dream/__init__.py` with:

```python
__version__ = "0.1.0"
```

Copy the Hermes MIT text into `LICENSE`, include the Nous Research copyright in `NOTICE`, and state in `README.md` that DREAM is an independent service derived in part from Hermes Agent 0.18.2.

- [ ] **Step 4: Install and run the package test**

Run: `python -m pip install -e '.[dev]'`

Expected: installation completes successfully.

Run: `python -m pytest tests/test_package.py -v`

Expected: `1 passed`.

- [ ] **Step 5: Commit the scaffold**

```bash
git add pyproject.toml README.md LICENSE NOTICE src/dream/__init__.py tests/test_package.py
git commit -m "chore: scaffold DREAM service"
```

### Task 2: Add strict tenant, agent, and user scope resolution

**Files:**
- Create: `src/dream/scope.py`
- Create: `tests/test_scope.py`

**Interfaces:**
- Consumes: `pathlib.Path`.
- Produces: `ScopeIds`, `ScopePaths`, and `resolve_scope(home: Path, ids: ScopeIds) -> ScopePaths`.

- [ ] **Step 1: Write scope isolation tests**

```python
from pathlib import Path

import pytest

from dream.scope import ScopeIds, resolve_scope


def test_resolve_scope_keeps_user_under_agent(tmp_path: Path) -> None:
    paths = resolve_scope(tmp_path, ScopeIds("acme", "assistant", "alice"))
    assert paths.user_root == (
        tmp_path / "tenants" / "acme" / "agents" / "assistant" / "users" / "alice"
    )
    assert paths.skills_dir == tmp_path / "tenants" / "acme" / "agents" / "assistant" / "skills"


@pytest.mark.parametrize("value", ["../bob", "/tmp/bob", "a/b", "", "."])
def test_scope_rejects_unsafe_identifiers(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        resolve_scope(tmp_path, ScopeIds("acme", "assistant", value))
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_scope.py -v`

Expected: FAIL because `dream.scope` does not exist.

- [ ] **Step 3: Implement the resolver**

```python
from dataclasses import dataclass
from pathlib import Path
import re


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class ScopeIds:
    tenant_id: str
    agent_id: str
    user_id: str


@dataclass(frozen=True)
class ScopePaths:
    agent_root: Path
    user_root: Path
    skills_dir: Path
    decision_cards_dir: Path
    snapshots_dir: Path
    reports_dir: Path


def _validate(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def resolve_scope(home: Path, ids: ScopeIds) -> ScopePaths:
    tenant = _validate(ids.tenant_id, "tenant_id")
    agent = _validate(ids.agent_id, "agent_id")
    user = _validate(ids.user_id, "user_id")
    agent_root = home / "tenants" / tenant / "agents" / agent
    user_root = agent_root / "users" / user
    return ScopePaths(
        agent_root=agent_root,
        user_root=user_root,
        skills_dir=agent_root / "skills",
        decision_cards_dir=agent_root / "decision-cards",
        snapshots_dir=agent_root / "snapshots",
        reports_dir=agent_root / "dream-reports",
    )
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_scope.py -v`

Expected: all scope tests PASS.

- [ ] **Step 5: Commit scope isolation**

```bash
git add src/dream/scope.py tests/test_scope.py
git commit -m "feat: add isolated memory scopes"
```

### Task 3: Add the append-only task event ledger

**Files:**
- Create: `src/dream/events.py`
- Create: `src/dream/ledger.py`
- Create: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `ScopeIds` and `TaskCompletedEvent`.
- Produces: `EventLedger.append(event) -> None` and `EventLedger.read_all() -> list[TaskCompletedEvent]`.

- [ ] **Step 1: Write an append-and-reload test**

```python
from pathlib import Path

from dream.events import TaskCompletedEvent
from dream.ledger import EventLedger
from dream.scope import ScopeIds


def test_event_ledger_is_append_only_and_reloadable(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "ledger" / "events.jsonl")
    event = TaskCompletedEvent(
        event_id="evt-1",
        task_id="task-1",
        scope=ScopeIds("acme", "assistant", "alice"),
        completed_at="2026-07-15T10:00:00+08:00",
        interrupted=False,
        tool_iterations=12,
        transcript=[{"role": "user", "content": "Prefer concise answers"}],
        final_response="Understood.",
        source_refs=[],
    )
    ledger.append(event)
    assert EventLedger(ledger.path).read_all() == [event]
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/test_ledger.py -v`

Expected: FAIL because the event and ledger modules do not exist.

- [ ] **Step 3: Implement typed events and locked JSONL append**

Define `TaskCompletedEvent` as a frozen dataclass. Serialize nested `ScopeIds` with `dataclasses.asdict`, write one compact JSON object per line, flush, and call `os.fsync`. Protect append in-process with `threading.Lock`; write the newline in the same file handle operation. Reject duplicate `event_id` values detected during load.

The public constructor must be:

```python
class EventLedger:
    def __init__(self, path: Path) -> None: ...
    def append(self, event: TaskCompletedEvent) -> None: ...
    def read_all(self) -> list[TaskCompletedEvent]: ...
```

- [ ] **Step 4: Run ledger tests**

Run: `python -m pytest tests/test_ledger.py -v`

Expected: `1 passed`.

- [ ] **Step 5: Commit the event ledger**

```bash
git add src/dream/events.py src/dream/ledger.py tests/test_ledger.py
git commit -m "feat: add append-only task ledger"
```

### Task 4: Add atomic scoped artifacts and frozen context manifests

**Files:**
- Create: `src/dream/artifacts.py`
- Create: `src/dream/snapshots.py`
- Create: `tests/test_snapshots.py`

**Interfaces:**
- Consumes: `ScopePaths` and relative artifact names.
- Produces: `AtomicArtifactStore.write_text(relative: Path, content: str) -> ArtifactVersion` and `SnapshotStore.create(scope: ScopeIds) -> ContextSnapshot`.

- [ ] **Step 1: Write the next-task activation test**

```python
from pathlib import Path

from dream.artifacts import AtomicArtifactStore
from dream.scope import ScopeIds, resolve_scope
from dream.snapshots import SnapshotStore


def test_background_write_does_not_mutate_existing_snapshot(tmp_path: Path) -> None:
    ids = ScopeIds("acme", "assistant", "alice")
    paths = resolve_scope(tmp_path, ids)
    store = AtomicArtifactStore(paths.agent_root)
    store.write_text(Path("users/alice/USER.md"), "prefers detail\n")
    snapshots = SnapshotStore(paths, store)
    first = snapshots.create(ids)

    store.write_text(Path("users/alice/USER.md"), "prefers concise answers\n")
    second = snapshots.create(ids)

    assert first.files["users/alice/USER.md"].content == "prefers detail\n"
    assert second.files["users/alice/USER.md"].content == "prefers concise answers\n"
    assert first.snapshot_id != second.snapshot_id
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/test_snapshots.py -v`

Expected: FAIL because artifact and snapshot modules do not exist.

- [ ] **Step 3: Implement atomic writes and immutable snapshots**

`AtomicArtifactStore` must reject absolute paths and any path containing `..`. Write to a sibling temporary file, flush and `fsync`, then replace the target with `os.replace`. Return an `ArtifactVersion` containing SHA-256, byte length and UTC timestamp.

`SnapshotStore.create` must read `SOUL.md`, `DECISION_RULES.md`, the current user's `USER.md`, `MEMORY.md`, `TODOS.md`, and a sorted skill manifest. Copy file contents into frozen dataclasses rather than retaining live `Path` objects. Compute `snapshot_id` from the canonical JSON representation of all file hashes.

- [ ] **Step 4: Run snapshot tests**

Run: `python -m pytest tests/test_snapshots.py -v`

Expected: `1 passed`.

- [ ] **Step 5: Commit artifact versioning**

```bash
git add src/dream/artifacts.py src/dream/snapshots.py tests/test_snapshots.py
git commit -m "feat: freeze task memory snapshots"
```

### Task 5: Port the Hermes review boundary and add deterministic classification

**Files:**
- Create: `src/dream/review/models.py`
- Create: `src/dream/review/backend.py`
- Create: `src/dream/review/classifier.py`
- Create: `src/dream/review/orchestrator.py`
- Create: `tests/review/test_orchestrator.py`

**Interfaces:**
- Consumes: `TaskCompletedEvent`, `ContextSnapshot`, and a `ReviewBackend`.
- Produces: `ReviewBackend.review(request) -> ReviewResult` and `BackgroundReviewOrchestrator.review(event, snapshot) -> ReviewResult`.

- [ ] **Step 1: Write restricted routing tests**

```python
from dream.review.backend import DeterministicReviewBackend
from dream.review.models import ArtifactKind, ReviewRequest


def test_review_routes_preference_without_exposing_unrelated_tools() -> None:
    backend = DeterministicReviewBackend()
    result = backend.review(
        ReviewRequest(
            event_id="evt-1",
            transcript_text="User: I prefer concise answers",
            final_response="Understood.",
            allowed_tools=frozenset({"memory_manage"}),
        )
    )
    assert result.actions[0].kind is ArtifactKind.USER_PROFILE
    assert result.actions[0].tool_name == "memory_manage"
    assert {action.tool_name for action in result.actions} <= {"memory_manage"}
```

- [ ] **Step 2: Run the review test and verify it fails**

Run: `python -m pytest tests/review/test_orchestrator.py -v`

Expected: FAIL because review modules do not exist.

- [ ] **Step 3: Implement the Hermes-compatible boundary**

Define:

```python
class ArtifactKind(str, Enum):
    DECISION_CARD = "decision_card"
    USER_PROFILE = "user_profile"
    USER_TODO = "user_todo"
    AGENT_MEMORY = "agent_memory"
    SKILL = "skill"
    WIKI_INGEST = "wiki_ingest"
    NOTHING = "nothing"


@dataclass(frozen=True)
class ReviewAction:
    kind: ArtifactKind
    tool_name: str
    payload: dict[str, object]
    source_event_id: str


@dataclass(frozen=True)
class ReviewResult:
    actions: tuple[ReviewAction, ...]
    summary: str
```

`BackgroundReviewOrchestrator` must copy Hermes behavior at the boundary: return immediately for interrupted events, run ReviewBackend outside the foreground request, pass a frozen transcript snapshot, filter actions to the request's `allowed_tools`, and turn backend exceptions into a failed review result rather than raising into the caller.

The deterministic backend is only the first-stage closure test. It recognizes the exact phrase `I prefer concise answers` and emits one `USER_PROFILE` action. It must emit `NOTHING` for unmatched text. The later LLM backend must implement the same protocol without changing callers.

- [ ] **Step 4: Run review tests**

Run: `python -m pytest tests/review/test_orchestrator.py -v`

Expected: all review tests PASS.

- [ ] **Step 5: Commit the review boundary**

```bash
git add src/dream/hermes_compat src/dream/review tests/review
git commit -m "feat: add Hermes-compatible review boundary"
```

### Task 6: Apply reviewed memory actions with reports and rollback

**Files:**
- Create: `src/dream/managers/memory.py`
- Create: `src/dream/reports.py`
- Create: `src/dream/rollback.py`
- Create: `tests/test_review_application.py`

**Interfaces:**
- Consumes: scoped `ReviewAction` values.
- Produces: `MemoryManager.apply(action) -> ArtifactVersion`, `DreamReportStore.write(report) -> Path`, and `RollbackService.restore(snapshot_id) -> None`.

- [ ] **Step 1: Write an end-to-end disk and rollback test**

```python
from pathlib import Path

from dream.managers.memory import MemoryManager
from dream.review.models import ArtifactKind, ReviewAction
from dream.scope import ScopeIds, resolve_scope


def test_user_memory_action_is_scoped_and_recoverable(tmp_path: Path) -> None:
    ids = ScopeIds("acme", "assistant", "alice")
    paths = resolve_scope(tmp_path, ids)
    manager = MemoryManager(paths)
    action = ReviewAction(
        kind=ArtifactKind.USER_PROFILE,
        tool_name="memory_manage",
        payload={"action": "add", "content": "Prefers concise answers."},
        source_event_id="evt-1",
    )
    version = manager.apply(action)
    assert version.sha256
    assert "evt-1" in (paths.user_root / "USER.md").read_text()
    assert not (paths.agent_root / "users" / "bob" / "USER.md").exists()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest tests/test_review_application.py -v`

Expected: FAIL because managers and reports do not exist.

- [ ] **Step 3: Implement the scoped memory adapter**

Port Hermes memory semantics for `add`, `replace`, and `remove`: exact duplicate prevention, bounded file size, entry delimiter, atomic replacement, and source metadata. Adapt only path resolution so `target=user` writes the current `user_root/USER.md`; `target=agent` writes `agent_root/MEMORY.md`. Do not accept a path in the action payload.

Before each mutation, copy the affected files into `snapshots/<snapshot_id>/`. Write a JSON report containing `run_id`, scope IDs, source event IDs, before hashes, after hashes, action summaries, status, errors and rollback snapshot ID.

`RollbackService.restore` must verify the requested snapshot belongs to the same agent scope, copy through `AtomicArtifactStore`, and create a rollback report.

- [ ] **Step 4: Run application and rollback tests**

Run: `python -m pytest tests/test_review_application.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit managed memory writes**

```bash
git add src/dream/managers src/dream/reports.py src/dream/rollback.py tests/test_review_application.py
git commit -m "feat: report and rollback dream writes"
```

### Task 7: Add queue scheduling and Curator hooks

**Files:**
- Create: `src/dream/scheduler.py`
- Create: `src/dream/curators/protocol.py`
- Create: `src/dream/curators/registry.py`
- Create: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: normal task-completion events and registered curator callbacks.
- Produces: `DreamScheduler.enqueue(event)`, `DreamScheduler.run_pending()`, and `CuratorRegistry.run_due(now)`.

- [ ] **Step 1: Write scheduling behavior tests**

```python
from dream.scheduler import DreamScheduler


def test_scheduler_ignores_interrupted_tasks(completed_event, interrupted_event) -> None:
    scheduler = DreamScheduler(review_threshold=10)
    scheduler.enqueue(interrupted_event)
    scheduler.enqueue(completed_event)
    assert scheduler.pending_event_ids() == (completed_event.event_id,)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_scheduler.py -v`

Expected: FAIL because scheduler modules do not exist.

- [ ] **Step 3: Implement Hermes-style trigger bookkeeping**

Maintain cumulative tool iterations per `(tenant_id, agent_id, user_id)`. Queue only successful non-interrupted tasks. Mark a scope ready when cumulative iterations reach the configured threshold; reset the counter only after the review job is accepted. Add idle and interval timestamps without sleeping in tests: every scheduling method accepts an injected clock.

Define a `Curator` protocol with `name`, `should_run(now)`, and `run(scope)` methods. Register names `skill`, `ai`, and `user`; phase one test doubles record calls, while later plans replace them with the three concrete Curators.

- [ ] **Step 4: Run scheduler tests**

Run: `python -m pytest tests/test_scheduler.py -v`

Expected: all scheduler tests PASS.

- [ ] **Step 5: Commit scheduling infrastructure**

```bash
git add src/dream/scheduler.py src/dream/curators tests/test_scheduler.py
git commit -m "feat: schedule background dream reviews"
```

### Task 8: Expose the closure through FastAPI and verify user isolation

**Files:**
- Create: `src/dream/api.py`
- Create: `src/dream/service.py`
- Create: `tests/test_api_e2e.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: HTTP task-start, task-complete, review-run and rollback requests.
- Produces: FastAPI `app` and independently testable disk artifacts.

- [ ] **Step 1: Write the API closure test**

```python
from fastapi.testclient import TestClient

from dream.api import create_app


def test_review_updates_only_next_task_and_only_current_user(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    scope = {"tenant_id": "acme", "agent_id": "assistant", "user_id": "alice"}
    first = client.post("/v1/tasks/start", json=scope).json()
    client.post(
        "/v1/tasks/complete",
        json={
            **scope,
            "event_id": "evt-1",
            "task_id": "task-1",
            "completed_at": "2026-07-15T10:00:00+08:00",
            "interrupted": False,
            "tool_iterations": 12,
            "transcript": [{"role": "user", "content": "I prefer concise answers"}],
            "final_response": "Understood.",
            "source_refs": [],
        },
    ).raise_for_status()
    client.post("/v1/dream/run-pending").raise_for_status()
    second = client.post("/v1/tasks/start", json=scope).json()
    bob = client.post(
        "/v1/tasks/start",
        json={"tenant_id": "acme", "agent_id": "assistant", "user_id": "bob"},
    ).json()

    assert first["snapshot_id"] != second["snapshot_id"]
    assert "Prefers concise answers" not in first["user_profile"]
    assert "Prefers concise answers" in second["user_profile"]
    assert "Prefers concise answers" not in bob["user_profile"]
```

- [ ] **Step 2: Run the API test and verify it fails**

Run: `python -m pytest tests/test_api_e2e.py -v`

Expected: FAIL because `dream.api` does not exist.

- [ ] **Step 3: Implement the service and endpoints**

Create these endpoints:

```text
POST /v1/tasks/start
POST /v1/tasks/complete
POST /v1/dream/run-pending
POST /v1/dream/rollback/{snapshot_id}
GET  /v1/dream/reports/{run_id}
```

`tasks/start` resolves scope and creates a frozen context snapshot. `tasks/complete` validates and appends the event, then enqueues it without waiting for review. `run-pending` is the synchronous development/operations hook; production startup runs the same scheduler worker in the FastAPI lifespan. All response models exclude physical filesystem paths.

Document startup as `uvicorn dream.api:app --host 127.0.0.1 --port 8765` and state that external exposure requires enterprise authentication and TLS in the deployment layer.

- [ ] **Step 4: Run the complete foundation suite**

Run: `python -m pytest -v`

Expected: all tests PASS, including the cross-user isolation and next-task activation test.

- [ ] **Step 5: Commit the foundation closure**

```bash
git add src/dream/api.py src/dream/service.py tests/test_api_e2e.py README.md
git commit -m "feat: complete DREAM foundation loop"
```

## Self-Review Results

- Spec coverage: phase-one scope, ledger, trigger, restricted review, snapshots, reports, rollback, next-task activation and isolation each map to a task.
- Deferred by design: concrete Skill Curator, AI Curator, User Curator and llm-wiki ingestion each receive a separate implementation plan after the foundation interfaces pass end-to-end tests.
- Type consistency: `ScopeIds`, `TaskCompletedEvent`, `ReviewAction`, `ReviewResult`, `ContextSnapshot` and `ArtifactVersion` are introduced before consumers.
- Placeholder scan: implementation steps specify concrete interfaces, validation rules, commands and expected outcomes; no unresolved implementation markers are used.
