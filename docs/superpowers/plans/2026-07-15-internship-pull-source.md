# Internship Pull Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a DREAM-only, cursor-based NDJSON pull adapter that imports completed conversations from the Internship memory service and feeds the existing AI decision-card and isolated user-profile dream pipeline.

**Architecture:** A focused `dream.sources.internship` module owns HTTP and NDJSON validation, while `dream.source_sync` owns cursor state, source-to-event mapping, and idempotent ingestion. `DreamService` gains durable processed-event recovery, and the FastAPI worker/manual endpoint invoke the source sync before the existing Background Review and Curators.

**Tech Stack:** Python 3.11-3.13, FastAPI, Pydantic 2, HTTPX 0.28, JSONL/NDJSON, pytest 9, pytest-asyncio, Ruff.

## Global Constraints

- Modify DREAM only; never read or write the Internship repository, Redis, Elasticsearch, Mirage, S3, or embedding index.
- The source is disabled by default and must not change existing behavior without explicit `.env` configuration.
- Import complete, completed user/assistant text rounds only; `/recall` is not an acceptable source.
- Preserve DREAM isolation: AI cards at `tenant_id/agent_id`, user profiles at `tenant_id/agent_id/user_id`.
- Persist no API key or complete upstream HTTP response in the ledger, reports, state, or logs.
- Advance a cursor only after its event is durable locally or confirmed as an existing duplicate.
- Add no new top-level repository entries.
- Follow red-green-refactor for every production behavior.

---

### Task 1: Source settings, NDJSON model, and HTTP client

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `src/dream/config.py`
- Create: `src/dream/sources/__init__.py`
- Create: `src/dream/sources/internship.py`
- Modify: `tests/test_config.py`
- Create: `tests/source_helpers.py`
- Create: `tests/sources/__init__.py`
- Create: `tests/sources/test_internship.py`

**Interfaces:**
- Consumes: `.env` values loaded by `load_settings(path)`.
- Produces: `InternshipSourceSettings`, `InternshipMessage`, `InternshipRecord`, `SourceFetchError`, `parse_ndjson(text)`, and `InternshipSourceClient.fetch(after, limit)`.

Create shared test data in `tests/source_helpers.py` after the production types exist:

```python
import json
from pathlib import Path

from dream.config import InternshipSourceSettings
from dream.events import TaskCompletedEvent
from dream.scope import ScopeIds
from dream.sources.internship import InternshipRecord


VALID_RECORD: dict[str, object] = {
    "cursor": "101",
    "event_id": "evt-101",
    "user_id": "alice",
    "session_id": "session-1",
    "round_id": "round-1",
    "completed_at": "2026-07-15T10:00:00Z",
    "messages": [
        {"role": "user", "content": "Prefer concise answers"},
        {"role": "assistant", "content": "Understood"},
    ],
    "final_response": "Understood",
}


def source_record(**overrides: object) -> InternshipRecord:
    return InternshipRecord.model_validate({**VALID_RECORD, **overrides})


def source_line(**overrides: object) -> str:
    return json.dumps({**VALID_RECORD, **overrides}, ensure_ascii=False)


def source_settings(**overrides: object) -> InternshipSourceSettings:
    values: dict[str, object] = {
        "enabled": True,
        "url": "https://memory.example/v1/memory/dream-export",
        "api_key": "secret",
        "tenant_id": "acme",
        "agent_id": "assistant",
        "batch_size": 100,
        "timeout_seconds": 5.0,
        "interval_seconds": 300,
    }
    return InternshipSourceSettings(**{**values, **overrides})


def write_source_env(
    tmp_path: Path,
    *,
    interval_seconds: int = 300,
) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DREAM_INTERNSHIP_SOURCE_ENABLED=true\n"
        "DREAM_INTERNSHIP_SOURCE_URL=https://memory.example/v1/memory/dream-export\n"
        "DREAM_INTERNSHIP_SOURCE_API_KEY=secret\n"
        "DREAM_INTERNSHIP_SOURCE_TENANT_ID=acme\n"
        "DREAM_INTERNSHIP_SOURCE_AGENT_ID=assistant\n"
        f"DREAM_INTERNSHIP_SOURCE_INTERVAL_SECONDS={interval_seconds}\n",
        encoding="utf-8",
    )
    return env_file


def local_event(event_id: str) -> TaskCompletedEvent:
    return TaskCompletedEvent(
        event_id=event_id,
        task_id="local-task",
        scope=ScopeIds("acme", "assistant", "alice"),
        completed_at="2026-07-15T10:00:00Z",
        interrupted=False,
        tool_iterations=10,
        transcript=(
            {"role": "user", "content": "Prefer concise answers"},
            {"role": "assistant", "content": "Understood"},
        ),
        final_response="Understood",
        source_refs=(),
    )
```

- [ ] **Step 1: Write failing configuration tests**

Add tests that define the exact environment contract:

```python
def test_internship_source_defaults_to_disabled(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / ".env")
    assert settings.internship_source.enabled is False


def test_internship_source_loads_pull_settings(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DREAM_INTERNSHIP_SOURCE_ENABLED=true\n"
        "DREAM_INTERNSHIP_SOURCE_URL=http://127.0.0.1:8000/v1/memory/dream-export\n"
        "DREAM_INTERNSHIP_SOURCE_API_KEY=source-secret\n"
        "DREAM_INTERNSHIP_SOURCE_TENANT_ID=acme\n"
        "DREAM_INTERNSHIP_SOURCE_AGENT_ID=assistant\n"
        "DREAM_INTERNSHIP_SOURCE_BATCH_SIZE=25\n"
        "DREAM_INTERNSHIP_SOURCE_TIMEOUT_SECONDS=7.5\n"
        "DREAM_INTERNSHIP_SOURCE_INTERVAL_SECONDS=120\n",
        encoding="utf-8",
    )
    source = load_settings(env_file).internship_source
    assert source.enabled is True
    assert source.url.endswith("/v1/memory/dream-export")
    assert source.api_key == "source-secret"
    assert source.tenant_id == "acme"
    assert source.agent_id == "assistant"
    assert source.batch_size == 25
    assert source.timeout_seconds == 7.5
    assert source.interval_seconds == 120
```

- [ ] **Step 2: Run the configuration tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_config.py
```

Expected: FAIL because `DreamSettings` has no `internship_source` field.

- [ ] **Step 3: Add source settings and validation**

Add this immutable settings type and field:

```python
@dataclass(frozen=True)
class InternshipSourceSettings:
    enabled: bool = False
    url: str = ""
    api_key: str = ""
    tenant_id: str = ""
    agent_id: str = ""
    batch_size: int = 100
    timeout_seconds: float = 15.0
    interval_seconds: int = 300

```

Add this field to the existing `DreamSettings` dataclass and import `field` from `dataclasses`:

```python
internship_source: InternshipSourceSettings = field(
    default_factory=InternshipSourceSettings
)
```

Implement strict parsers:

```python
def _boolean(value: str, name: str) -> bool:
    normalized = value.casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _positive_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed
```

Build `InternshipSourceSettings` in `load_settings()`:

```python
source = InternshipSourceSettings(
    enabled=_boolean(
        value("DREAM_INTERNSHIP_SOURCE_ENABLED", "false"),
        "DREAM_INTERNSHIP_SOURCE_ENABLED",
    ),
    url=value("DREAM_INTERNSHIP_SOURCE_URL"),
    api_key=value("DREAM_INTERNSHIP_SOURCE_API_KEY"),
    tenant_id=value("DREAM_INTERNSHIP_SOURCE_TENANT_ID"),
    agent_id=value("DREAM_INTERNSHIP_SOURCE_AGENT_ID"),
    batch_size=_positive_int(
        value("DREAM_INTERNSHIP_SOURCE_BATCH_SIZE", "100"),
        "DREAM_INTERNSHIP_SOURCE_BATCH_SIZE",
    ),
    timeout_seconds=_positive_float(
        value("DREAM_INTERNSHIP_SOURCE_TIMEOUT_SECONDS", "15"),
        "DREAM_INTERNSHIP_SOURCE_TIMEOUT_SECONDS",
    ),
    interval_seconds=_positive_int(
        value("DREAM_INTERNSHIP_SOURCE_INTERVAL_SECONDS", "300"),
        "DREAM_INTERNSHIP_SOURCE_INTERVAL_SECONDS",
    ),
)
if source.enabled and not all((source.url, source.tenant_id, source.agent_id)):
    raise ValueError(
        "enabled Internship source requires URL, tenant ID, and agent ID"
    )
```

Pass `internship_source=source` into the existing `DreamSettings(...)` return value.

- [ ] **Step 4: Run the configuration tests and verify GREEN**

Run the command from Step 2. Expected: all `tests/test_config.py` tests pass.

- [ ] **Step 5: Write failing NDJSON/client tests**

Create `tests/sources/test_internship.py` with real parsing and an HTTPX mock transport:

```python
import httpx
import pytest

from dream.sources.internship import (
    InternshipSourceClient,
    SourceFetchError,
    parse_ndjson,
)
from tests.source_helpers import source_line, source_settings


def test_parse_ndjson_accepts_records_and_empty_lines() -> None:
    records = parse_ndjson("\n" + source_line() + "\n")
    assert len(records) == 1
    assert records[0].cursor == "101"
    assert records[0].messages[0].role == "user"


def test_parse_ndjson_empty_body_means_no_new_records() -> None:
    assert parse_ndjson("") == ()


def test_parse_ndjson_rejects_missing_required_field() -> None:
    with pytest.raises(SourceFetchError, match="line 1"):
        parse_ndjson('{"cursor":"101"}\n')


def test_parse_ndjson_rejects_completed_at_without_timezone() -> None:
    with pytest.raises(SourceFetchError, match="line 1"):
        parse_ndjson(source_line(completed_at="2026-07-15T10:00:00"))


def test_client_sends_cursor_limit_accept_and_bearer_token() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["accept"] = request.headers.get("accept")
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, text=source_line() + "\n")

    settings = source_settings(batch_size=20, interval_seconds=60)
    client = InternshipSourceClient(
        settings, transport=httpx.MockTransport(handler)
    )
    records = client.fetch(after="100", limit=20)
    assert len(records) == 1
    assert captured == {
        "url": "https://memory.example/v1/memory/dream-export?after=100&limit=20",
        "accept": "application/x-ndjson",
        "authorization": "Bearer secret",
    }


def test_client_rejects_non_success_without_leaking_response_body() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(500, text="secret upstream body")
    )
    settings = source_settings(url="https://memory.example/export", api_key="")
    with pytest.raises(SourceFetchError) as error:
        InternshipSourceClient(settings, transport=transport).fetch("", 100)
    assert "secret upstream body" not in str(error.value)


def test_client_wraps_timeout_without_leaking_request_headers() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = InternshipSourceClient(
        source_settings(), transport=httpx.MockTransport(timeout)
    )
    with pytest.raises(SourceFetchError, match="request failed") as error:
        client.fetch("100", 20)
    assert "Bearer secret" not in str(error.value)
```

- [ ] **Step 6: Run source tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/sources/test_internship.py
```

Expected: FAIL because `dream.sources.internship` does not exist.

- [ ] **Step 7: Implement the source record and client**

Define the source models and timezone validation:

```python
class InternshipMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1)


class InternshipRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cursor: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    messages: list[InternshipMessage] = Field(min_length=1)
    final_response: str = Field(min_length=1)

    @field_validator("completed_at")
    @classmethod
    def completed_at_must_include_timezone(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("completed_at must be ISO 8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("completed_at must include a timezone")
        return value
```

Implement parsing and HTTP transport:

```python
class SourceFetchError(RuntimeError):
    pass


def parse_ndjson(text: str) -> tuple[InternshipRecord, ...]:
    records: list[InternshipRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(InternshipRecord.model_validate_json(line))
        except (ValueError, ValidationError) as exc:
            raise SourceFetchError(
                f"invalid Internship NDJSON record at line {line_number}"
            ) from exc
    return tuple(records)


class InternshipSourceClient:
    def __init__(
        self,
        settings: InternshipSourceSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    def fetch(self, after: str, limit: int) -> tuple[InternshipRecord, ...]:
        headers = {"Accept": "application/x-ndjson"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            with httpx.Client(
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.get(
                    self.settings.url,
                    params={"after": after, "limit": limit},
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Internship source request failed") from exc
        return parse_ndjson(response.text)
```

Move `httpx>=0.28,<1` from optional dev dependencies into runtime dependencies, and add the exact source variables from the design to `.env.example`.

- [ ] **Step 8: Run source/config tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_config.py tests/sources/test_internship.py
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add pyproject.toml .env.example src/dream/config.py src/dream/sources tests/test_config.py tests/sources
git commit -m "feat: add Internship NDJSON source client"
```

---

### Task 2: Cursor state, user mapping, and idempotent event ingestion

**Files:**
- Modify: `src/dream/ledger.py`
- Create: `src/dream/source_sync.py`
- Modify: `tests/test_ledger.py`
- Create: `tests/test_source_sync.py`

**Interfaces:**
- Consumes: `InternshipSourceSettings`, `InternshipRecord`, `InternshipSourceClient.fetch()`, `DreamService.ingest_conversation()`, and `EventLedger`.
- Produces: `normalize_source_user_id(value)`, `record_to_event(record, settings)`, `SourceSyncStateStore`, `SourceSyncResult`, and `InternshipSourceSync.sync_once()`.

- [ ] **Step 1: Write failing ledger/id mapping tests**

Add:

```python
def test_event_ledger_reports_existing_event_id(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.append(_event("evt-1"))
    assert ledger.contains("evt-1") is True
    assert ledger.contains("evt-2") is False
```

Create source-sync unit tests:

```python
def test_source_user_id_mapping_is_stable_and_path_safe() -> None:
    assert normalize_source_user_id("alice") == "alice"
    mapped = normalize_source_user_id("tenant:user@example.com")
    assert mapped.startswith("external-")
    assert mapped == normalize_source_user_id("tenant:user@example.com")
    assert "/" not in mapped and ":" not in mapped


def test_record_maps_to_configured_scope() -> None:
    event = record_to_event(source_record(user_id="alice"), source_settings())
    assert event.scope == ScopeIds("acme", "assistant", "alice")
    assert event.event_id.startswith("internship-")
    assert event.task_id == "session-1:round-1"
    assert event.final_response == "Understood"
    assert event.transcript[0]["role"] == "user"
```

- [ ] **Step 2: Run mapping tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ledger.py tests/test_source_sync.py
```

Expected: FAIL because `contains` and `dream.source_sync` do not exist.

- [ ] **Step 3: Implement ledger lookup and deterministic mapping**

Add `EventLedger.contains(event_id: str) -> bool`. Implement mapping with the existing DREAM safe-ID regular expression semantics:

```python
_SAFE_SOURCE_USER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def normalize_source_user_id(value: str) -> str:
    if _SAFE_SOURCE_USER_ID.fullmatch(value):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"external-{digest}"


def record_to_event(
    record: InternshipRecord,
    settings: InternshipSourceSettings,
) -> TaskCompletedEvent:
    source_event_hash = hashlib.sha256(
        record.event_id.encode("utf-8")
    ).hexdigest()[:32]
    return TaskCompletedEvent(
        event_id=f"internship-{source_event_hash}",
        task_id=f"{record.session_id}:{record.round_id}",
        scope=ScopeIds(
            settings.tenant_id,
            settings.agent_id,
            normalize_source_user_id(record.user_id),
        ),
        completed_at=record.completed_at,
        interrupted=False,
        tool_iterations=0,
        transcript=tuple(message.model_dump() for message in record.messages),
        final_response=record.final_response,
        source_refs=(
            {
                "source": "internship",
                "source_event_id": record.event_id,
                "source_user_id": record.user_id,
                "cursor": record.cursor,
            },
        ),
    )
```

- [ ] **Step 4: Run mapping tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Write failing cursor and duplicate tests**

Use a fake source client returning deterministic records and a real `DreamService(tmp_path)`:

```python
class FakeSourceClient:
    def __init__(self, records: list[InternshipRecord]) -> None:
        self.records = tuple(records)

    def fetch(self, after: str, limit: int) -> tuple[InternshipRecord, ...]:
        return self.records[:limit]


class FailingSourceClient:
    def fetch(self, after: str, limit: int) -> tuple[InternshipRecord, ...]:
        raise SourceFetchError("Internship source request failed")


def test_sync_persists_cursor_after_each_durable_event(tmp_path: Path) -> None:
    service = DreamService(tmp_path)
    sync = InternshipSourceSync(
        service,
        source_settings(),
        client=FakeSourceClient([
            source_record(cursor="101"),
            source_record(cursor="102", event_id="evt-102"),
        ]),
    )
    result = sync.sync_once()
    assert result.status == "success"
    assert result.ingested == 2
    assert result.cursor == "102"
    state = SourceSyncStateStore(tmp_path / "source-state" / "internship.json").load()
    assert state.cursor == "102"
    assert len(service.ledger.read_all()) == 2


def test_sync_advances_over_duplicate_without_reingesting(tmp_path: Path) -> None:
    service = DreamService(tmp_path)
    first = InternshipSourceSync(
        service,
        source_settings(),
        client=FakeSourceClient([source_record(cursor="101")]),
    )
    assert first.sync_once().ingested == 1
    SourceSyncStateStore(tmp_path / "source-state" / "internship.json").save(
        SourceSyncState(cursor="", last_sync_at="", last_status="success")
    )
    second = InternshipSourceSync(
        service,
        source_settings(),
        client=FakeSourceClient([source_record(cursor="101")]),
    )
    result = second.sync_once()
    assert result.duplicates == 1
    assert result.cursor == "101"
    assert len(service.ledger.read_all()) == 1


def test_sync_failure_does_not_advance_cursor(tmp_path: Path) -> None:
    service = DreamService(tmp_path)
    sync = InternshipSourceSync(
        service, source_settings(), client=FailingSourceClient()
    )
    result = sync.sync_once()
    assert result.status == "error"
    assert result.cursor == ""
    assert result.errors == ("Internship source request failed",)
```

- [ ] **Step 6: Run cursor tests and verify RED**

Run `tests/test_source_sync.py`. Expected: FAIL because sync/state classes do not exist.

- [ ] **Step 7: Implement atomic state and sync orchestration**

Define immutable state/result types:

```python
@dataclass(frozen=True)
class SourceSyncState:
    cursor: str = ""
    last_sync_at: str = ""
    last_status: str = "never"
    last_fetched: int = 0
    last_ingested: int = 0
    last_duplicates: int = 0
    last_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceSyncResult:
    status: str
    fetched: int
    ingested: int
    duplicates: int
    cursor: str
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

Implement the state store with same-directory atomic replacement:

```python
class SourceSyncStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SourceSyncState:
        if not self.path.exists():
            return SourceSyncState()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["last_errors"] = tuple(raw.get("last_errors", ()))
        return SourceSyncState(**raw)

    def save(self, state: SourceSyncState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(asdict(state), handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
```

Default `InternshipSourceSync.state_store` to `service.home / "source-state" / "internship.json"`. Implement `InternshipSourceSync.sync_once()` as follows:

```python
def sync_once(self) -> SourceSyncResult:
    state = self.state_store.load()
    now = datetime.now(timezone.utc).isoformat()
    try:
        records = self.client.fetch(
            after=state.cursor,
            limit=self.settings.batch_size,
        )
    except SourceFetchError as exc:
        result = SourceSyncResult(
            status="error",
            fetched=0,
            ingested=0,
            duplicates=0,
            cursor=state.cursor,
            errors=(str(exc),),
        )
        self.state_store.save(
            SourceSyncState(
                cursor=state.cursor,
                last_sync_at=now,
                last_status=result.status,
                last_errors=result.errors,
            )
        )
        return result

    ingested = 0
    duplicates = 0
    cursor = state.cursor
    for record in records:
        try:
            event = record_to_event(record, self.settings)
            if self.service.ledger.contains(event.event_id):
                duplicates += 1
            else:
                self.service.ingest_conversation(event)
                ingested += 1
        except ValueError as exc:
            result = SourceSyncResult(
                status="error",
                fetched=len(records),
                ingested=ingested,
                duplicates=duplicates,
                cursor=cursor,
                errors=(str(exc),),
            )
            self.state_store.save(
                SourceSyncState(
                    cursor=cursor,
                    last_sync_at=now,
                    last_status=result.status,
                    last_fetched=result.fetched,
                    last_ingested=result.ingested,
                    last_duplicates=result.duplicates,
                    last_errors=result.errors,
                )
            )
            return result
        cursor = record.cursor
        self.state_store.save(
            SourceSyncState(
                cursor=cursor,
                last_sync_at=now,
                last_status="success",
                last_fetched=len(records),
                last_ingested=ingested,
                last_duplicates=duplicates,
            )
        )
    result = SourceSyncResult(
        status="success",
        fetched=len(records),
        ingested=ingested,
        duplicates=duplicates,
        cursor=cursor,
    )
    self.state_store.save(
        SourceSyncState(
            cursor=cursor,
            last_sync_at=now,
            last_status=result.status,
            last_fetched=result.fetched,
            last_ingested=result.ingested,
            last_duplicates=result.duplicates,
        )
    )
    return result
```

The final state JSON includes these counters but never the API key or records.

- [ ] **Step 8: Run source-sync tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_ledger.py tests/test_source_sync.py
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit Task 2**

```bash
git add src/dream/ledger.py src/dream/source_sync.py tests/test_ledger.py tests/test_source_sync.py
git commit -m "feat: import source events with durable cursors"
```

---

### Task 3: Durable processed-event recovery

**Files:**
- Create: `src/dream/processed_events.py`
- Modify: `src/dream/service.py`
- Create: `tests/test_processed_events.py`
- Modify: `tests/test_review_application.py`

**Interfaces:**
- Consumes: the append-only event ledger and `DreamScheduler`.
- Produces: `ProcessedEventLedger.contains()`, `ProcessedEventLedger.append()`, and `DreamService.recover_pending_events()`.

- [ ] **Step 1: Write failing processed-ledger tests**

```python
def test_processed_event_ledger_is_append_only_and_idempotent(tmp_path: Path) -> None:
    ledger = ProcessedEventLedger(tmp_path / "processed-events.jsonl")
    ledger.append("evt-1")
    ledger.append("evt-1")
    assert ledger.contains("evt-1") is True
    assert ledger.read_all() == ("evt-1",)
```

- [ ] **Step 2: Run the processed-ledger test and verify RED**

Run `tests/test_processed_events.py`. Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement processed-event persistence**

Use an append-only JSONL file with an `RLock`, duplicate suppression, `flush()`, and `os.fsync()`:

```python
class ProcessedEventLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def read_all(self) -> tuple[str, ...]:
        if not self.path.exists():
            return ()
        values = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                values.append(str(json.loads(line)["event_id"]))
        return tuple(dict.fromkeys(values))

    def contains(self, event_id: str) -> bool:
        return event_id in self.read_all()

    def append(self, event_id: str) -> None:
        with self._lock:
            if self.contains(event_id):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event_id": event_id}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
```

- [ ] **Step 4: Run the processed-ledger test and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Write failing restart-recovery tests**

Add tests proving an unprocessed event is recovered and a processed one is not:

```python
def test_service_recovers_unprocessed_ledger_event_after_restart(tmp_path: Path) -> None:
    first = DreamService(tmp_path)
    first.ingest_conversation(local_event("evt-1"))

    restarted = DreamService(tmp_path)
    assert restarted.scheduler.pending_event_ids() == ("evt-1",)
    assert restarted.run_pending()[0]["status"] == "success"

    finished = DreamService(tmp_path)
    assert finished.scheduler.pending_event_ids() == ()


def test_run_pending_marks_event_processed_after_report(tmp_path: Path) -> None:
    service = DreamService(tmp_path)
    service.ingest_conversation(local_event("evt-1"))
    service.run_pending()
    assert service.processed_events.contains("evt-1") is True
```

- [ ] **Step 6: Run recovery tests and verify RED**

Run `tests/test_review_application.py`. Expected: FAIL because service recovery and processed state do not exist.

- [ ] **Step 7: Implement recovery in `DreamService`**

Initialize processed state and recover in the constructor:

```python
self.processed_events = ProcessedEventLedger(
    home / "ledger" / "processed-events.jsonl"
)
self.recover_pending_events()
```

Implement:

```python
def recover_pending_events(self) -> int:
    pending = set(self.scheduler.pending_event_ids())
    recovered = 0
    for event in self.ledger.read_all():
        if event.event_id in pending:
            continue
        if self.processed_events.contains(event.event_id):
            continue
        self.scheduler.enqueue(event)
        pending.add(event.event_id)
        recovered += 1
    return recovered
```

In `run_pending()`, append the event ID only after the report write succeeds and the run result has been assembled. Current `partial` behavior remains terminal and is marked processed; its errors remain available in the dream report.

- [ ] **Step 8: Run recovery tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_processed_events.py tests/test_review_application.py tests/test_source_sync.py
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit Task 3**

```bash
git add src/dream/processed_events.py src/dream/service.py tests/test_processed_events.py tests/test_review_application.py
git commit -m "fix: recover unprocessed dream events after restart"
```

---

### Task 4: Manual sync API and periodic worker integration

**Files:**
- Modify: `src/dream/api.py`
- Modify: `src/dream/source_sync.py`
- Create: `tests/test_source_api.py`
- Modify: `tests/test_api_e2e.py`

**Interfaces:**
- Consumes: configured `InternshipSourceClient`, `InternshipSourceSync`, `DreamService.run_pending()`, and the existing lifespan worker.
- Produces: `POST /v1/sources/internship/sync`, `InternshipSourceSync.sync_if_due(now)`, and `application.state.internship_source_sync`.

- [ ] **Step 1: Write failing disabled and manual-sync API tests**

```python
@pytest.mark.asyncio
async def test_manual_source_sync_reports_disabled_by_default(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(app=create_app(tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://dream.test") as client:
        response = await client.post("/v1/sources/internship/sync")
    assert response.status_code == 200
    assert response.json() == {"status": "disabled"}


@pytest.mark.asyncio
async def test_manual_source_sync_imports_records(tmp_path: Path) -> None:
    env_file = write_source_env(tmp_path)
    source_transport = httpx.MockTransport(
        lambda _: httpx.Response(200, text=source_line() + "\n")
    )
    app = create_app(
        tmp_path,
        env_file=env_file,
        source_transport=source_transport,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dream.test") as client:
        response = await client.post("/v1/sources/internship/sync")
        run = await client.post("/v1/dream/run-pending")
    assert response.status_code == 200
    assert response.json()["ingested"] == 1
    assert run.json()["runs"][0]["artifact_kinds"]


@pytest.mark.asyncio
async def test_manual_source_sync_returns_bad_gateway_on_source_failure(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        env_file=write_source_env(tmp_path),
        source_transport=httpx.MockTransport(
            lambda _: httpx.Response(500, text="private upstream body")
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://dream.test"
    ) as client:
        response = await client.post("/v1/sources/internship/sync")
    assert response.status_code == 502
    assert "private upstream body" not in response.text
```

- [ ] **Step 2: Run source API tests and verify RED**

Run `tests/test_source_api.py`. Expected: FAIL with 404 and unknown `source_transport`.

- [ ] **Step 3: Add sync construction and manual endpoint**

Extend `create_app()` with:

```python
def create_app(
    home: Path,
    worker_interval_seconds: float = 60.0,
    *,
    env_file: Path | None = None,
    client_factory: Callable[..., object] | None = None,
    source_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
```

Construct the optional source sync before the lifespan definition:

```python
source_sync: InternshipSourceSync | None = None
if settings.internship_source.enabled:
    source_client = InternshipSourceClient(
        settings.internship_source,
        transport=source_transport,
    )
    source_sync = InternshipSourceSync(
        service,
        settings.internship_source,
        client=source_client,
    )
```

After creating `application`, expose it for inspection:

```python
application.state.internship_source_sync = source_sync
```

Add:

```python
@application.post("/v1/sources/internship/sync")
def sync_internship_source() -> Response | dict[str, object]:
    if source_sync is None:
        return {"status": "disabled"}
    result = source_sync.sync_once()
    if result.status == "error":
        return JSONResponse(status_code=502, content=result.to_dict())
    return result.to_dict()
```

- [ ] **Step 4: Run manual API tests and verify GREEN**

Run `tests/test_source_api.py`. Expected: selected tests pass.

- [ ] **Step 5: Write failing due-interval and worker tests**

Test `sync_if_due(now)` with a fixed state timestamp:

```python
class FailIfCalledClient:
    def fetch(self, after: str, limit: int) -> tuple[InternshipRecord, ...]:
        raise AssertionError("source client must not be called before interval")


def test_sync_if_due_skips_before_interval(tmp_path: Path) -> None:
    state_store = SourceSyncStateStore(tmp_path / "state.json")
    state_store.save(
        SourceSyncState(
            cursor="101",
            last_sync_at="2026-07-15T10:00:00+00:00",
            last_status="success",
        )
    )
    sync = InternshipSourceSync(
        DreamService(tmp_path / "dream"),
        source_settings(interval_seconds=300),
        client=FailIfCalledClient(),
        state_store=state_store,
    )
    result = sync.sync_if_due(datetime.fromisoformat("2026-07-15T10:04:59+00:00"))
    assert result.status == "not_due"
    assert result.cursor == "101"
```

Add complete lifespan tests using `TestClient`, which activates FastAPI lifespan:

```python
def test_worker_pulls_source_and_processes_dream(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = source_line() + "\n" if calls == 1 else ""
        return httpx.Response(200, text=body)

    app = create_app(
        tmp_path,
        worker_interval_seconds=0.01,
        env_file=write_source_env(tmp_path, interval_seconds=1),
        source_transport=httpx.MockTransport(handler),
    )
    with TestClient(app):
        for _ in range(50):
            profile = tmp_path / "tenants/acme/agents/assistant/users/alice/USER.md"
            if profile.exists():
                break
            time.sleep(0.01)
    assert profile.exists()
    assert calls >= 1


def test_worker_processes_local_queue_when_source_fails(tmp_path: Path) -> None:
    app = create_app(
        tmp_path,
        worker_interval_seconds=0.01,
        env_file=write_source_env(tmp_path, interval_seconds=1),
        source_transport=httpx.MockTransport(
            lambda _: httpx.Response(500, text="upstream unavailable")
        ),
    )
    app.state.dream_service.ingest_conversation(local_event("local-evt"))
    with TestClient(app):
        for _ in range(50):
            if app.state.dream_service.processed_events.contains("local-evt"):
                break
            time.sleep(0.01)
    assert app.state.dream_service.processed_events.contains("local-evt")
```

- [ ] **Step 6: Run due/worker tests and verify RED**

Run `tests/test_source_sync.py tests/test_api_e2e.py`. Expected: FAIL because `sync_if_due()` and worker source invocation do not exist.

- [ ] **Step 7: Implement due checks and worker ordering**

Implement timezone-aware interval checks:

```python
def sync_if_due(self, now: datetime) -> SourceSyncResult:
    state = self.state_store.load()
    if state.last_sync_at:
        last_sync = datetime.fromisoformat(state.last_sync_at)
        if now - last_sync < timedelta(seconds=self.settings.interval_seconds):
            return SourceSyncResult(
                status="not_due",
                fetched=0,
                ingested=0,
                duplicates=0,
                cursor=state.cursor,
            )
    return self.sync_once()
```

In each worker iteration:

```python
now = datetime.now(timezone.utc)
if source_sync is not None:
    source_result = await asyncio.to_thread(source_sync.sync_if_due, now)
    if source_result.status == "error":
        logger.error("Internship source sync failed: %s", source_result.errors)
await asyncio.to_thread(service.recover_pending_events)
await asyncio.to_thread(service.run_pending)
await asyncio.to_thread(service.run_due_curators, now)
```

Keep source failure isolated so local pending dreams and curators still execute.

- [ ] **Step 8: Run due/worker tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_source_sync.py tests/test_source_api.py tests/test_api_e2e.py
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit Task 4**

```bash
git add src/dream/api.py src/dream/source_sync.py tests/test_source_api.py tests/test_source_sync.py tests/test_api_e2e.py
git commit -m "feat: schedule Internship memory pulls"
```

---

### Task 5: Two-user dream acceptance, documentation, and final verification

**Files:**
- Modify: `src/dream/hermes_compat/prompts.py`
- Modify: `tests/review/test_orchestrator.py`
- Modify: `tests/test_source_api.py`
- Modify: `README.md`
- Modify: `docs/api/short-term-memory-contract.md`

**Interfaces:**
- Consumes: the completed source sync and existing `/v1/tasks/start` snapshot endpoint.
- Produces: an explicit privacy boundary for shared cards, an end-to-end acceptance test, and operator-facing setup instructions.

- [ ] **Step 1: Write the failing shared-card privacy prompt test**

```python
from dream.hermes_compat.prompts import DREAM_COMBINED_REVIEW_PROMPT


def test_background_review_prompt_keeps_user_facts_out_of_shared_cards() -> None:
    assert (
        "Never copy a user's personal facts, identity, preferences, or secrets "
        "into an AI decision card"
        in DREAM_COMBINED_REVIEW_PROMPT
    )
```

- [ ] **Step 2: Run the privacy prompt test and verify RED**

Run this test only. Expected: FAIL because the prompt does not yet state the shared-card privacy boundary explicitly.

- [ ] **Step 3: Add the shared-card privacy boundary and verify GREEN**

Add this exact paragraph beneath the AI decision-card guidance:

```text
Never copy a user's personal facts, identity, preferences, or secrets into an
AI decision card. Cards may generalize interaction lessons, but they must
remain user-agnostic.
```

Run the privacy prompt test again. Expected: PASS.

- [ ] **Step 4: Write the two-user acceptance test**

Return two NDJSON records for `alice` and `bob`. Alice contains a durable preference and an assistant decision signal; Bob contains unrelated conversation. Through HTTP, call manual sync, run pending, then request both task contexts:

```python
def _two_user_source(_: httpx.Request) -> httpx.Response:
    alice = source_line(
        cursor="101",
        event_id="evt-alice",
        user_id="alice",
        messages=[
            {"role": "user", "content": "I prefer concise answers"},
            {
                "role": "assistant",
                "content": "I will verify before risky action because it is irreversible",
            },
        ],
        final_response="I will verify before risky action.",
    )
    bob = source_line(
        cursor="102",
        event_id="evt-bob",
        user_id="bob",
        session_id="session-2",
        round_id="round-2",
        messages=[
            {"role": "user", "content": "What time is it?"},
            {"role": "assistant", "content": "It is 10:00."},
        ],
        final_response="It is 10:00.",
    )
    return httpx.Response(200, text=f"{alice}\n{bob}\n")


@pytest.mark.asyncio
async def test_source_pull_creates_shared_ai_cards_and_isolated_profiles(
    tmp_path: Path,
) -> None:
    app = create_app(
        tmp_path,
        env_file=write_source_env(tmp_path),
        source_transport=httpx.MockTransport(_two_user_source),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dream.test") as client:
        assert (await client.post("/v1/sources/internship/sync")).status_code == 200
        await client.post("/v1/dream/run-pending")
        alice = (
            await client.post(
                "/v1/tasks/start",
                json={"tenant_id": "acme", "agent_id": "assistant", "user_id": "alice"},
            )
        ).json()
        bob = (
            await client.post(
                "/v1/tasks/start",
                json={"tenant_id": "acme", "agent_id": "assistant", "user_id": "bob"},
            )
        ).json()
    assert "Prefers concise answers" in alice["user_profile"]
    assert "Prefers concise answers" not in bob["user_profile"]
    assert alice["decision_cards"] == bob["decision_cards"]
    assert alice["decision_cards"]
```

- [ ] **Step 5: Run the acceptance test**

Run only this test. Expected: PASS because every production unit completed its RED cycle in Tasks 1-4. If it fails, return to the failing unit test in the owning task before changing production code.

- [ ] **Step 6: Document source setup and data contract**

Add README commands:

```env
DREAM_INTERNSHIP_SOURCE_ENABLED=true
DREAM_INTERNSHIP_SOURCE_URL=https://memory.example.com/v1/memory/dream-export
DREAM_INTERNSHIP_SOURCE_API_KEY=replace-locally
DREAM_INTERNSHIP_SOURCE_TENANT_ID=acme
DREAM_INTERNSHIP_SOURCE_AGENT_ID=assistant
```

Document manual verification:

```bash
curl -X POST http://127.0.0.1:8765/v1/sources/internship/sync
```

Document that `/v1/memory/recall`, GitHub, Redis, Mirage, ES, and embeddings are not used for this integration. Add the required NDJSON fields and cursor semantics to `docs/api/short-term-memory-contract.md`.

- [ ] **Step 7: Run full verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest -q -p no:cacheprovider
../.venv/bin/python -m ruff check --no-cache src tests
../.venv/bin/python -m pip check
git diff --check
```

Expected: all tests pass, Ruff reports `All checks passed!`, pip reports `No broken requirements found.`, and `git diff --check` produces no output.

- [ ] **Step 8: Verify repository cleanliness constraints**

Run:

```bash
find . -mindepth 1 -maxdepth 1 -print | sort
find src tests -type d \( -name __pycache__ -o -name '*.egg-info' \) -print
```

Expected top-level entries remain exactly `.env.example`, `.git`, `.gitignore`, `README.md`, `docs`, `pyproject.toml`, `src`, and `tests`; the second command produces no output after cache cleanup.

- [ ] **Step 9: Commit Task 5**

```bash
git add README.md docs/api/short-term-memory-contract.md src/dream/hermes_compat/prompts.py tests/review/test_orchestrator.py tests/test_source_api.py
git commit -m "docs: explain Internship memory source integration"
```

- [ ] **Step 10: Report handoff information**

Report the final test count, commit IDs, required `.env` values, the manual sync endpoint, and the single remaining external dependency: the colleague must provide the real export URL and read-only credential.
