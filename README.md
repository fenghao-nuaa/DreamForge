# DREAM

DREAM is a standalone, disk-verifiable memory refinement service derived in
part from Hermes Agent 0.18.2. It receives completed agent tasks, runs a
Hermes-compatible background review, and makes reviewed memory available to
the next task through immutable context snapshots.

The current implementation provides the event ledger, tenant/user scope
isolation, background scheduling, snapshots, reports, rollback, AI decision
cards, and isolated user profiles.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest -v
```

Run the local-only API with:

```bash
uvicorn dream.api:app --host 127.0.0.1 --port 8765
```

Completed conversations can enter DREAM in two ways: an upstream service can
push one batch to `POST /v1/dream/conversations`, or DREAM can periodically pull
NDJSON from a configured export API. DREAM does not connect to the upstream
Redis, Mirage, vector index, or embedding store. The background worker saves a
durable cursor, fetches only newer records, converts them into DREAM events,
and then runs the same Background Review used by pushed conversations.

Enable the pull source with the `DREAM_INTERNSHIP_SOURCE_*` values shown in
`.env.example`. The upstream endpoint receives `after` and `limit` query
parameters and returns `application/x-ndjson`. Each line contains `cursor`,
`event_id`, `user_id`, `session_id`, `round_id`, `completed_at`, complete
`messages`, and `final_response`.

The current priority artifacts are agent-level AI decision cards and isolated
per-user `USER.md` profiles. A pulled conversation can update both in one
Background Review. `POST /v1/dream/run-curators` performs the periodic
maintenance dream for both artifact types. Context returned from
`POST /v1/tasks/start` is a frozen snapshot, so a dream write becomes visible
only to the next task.

Bind to a public interface only behind enterprise authentication and TLS.

## LLM configuration

Copy `.env.example` to `.env`, choose an OpenAI-compatible model, and replace
the example secret locally. `.env` is ignored by Git:

```bash
cp .env.example .env
uvicorn dream.api:app --host 127.0.0.1 --port 8765
```

With `DREAM_REVIEW_BACKEND=openai`, the Background Review uses the
Hermes-derived combined prompt and may call only `memory_manage` and
`decision_card_manage`. With `DREAM_CURATOR_BACKEND=inherit`, AI and User
Curators use the same provider/model for semantic consolidation. The
deterministic backend remains available for tests and offline closure checks.

The upstream integration payload and isolation contract are documented in
`docs/api/short-term-memory-contract.md`.
