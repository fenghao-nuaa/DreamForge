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

The upstream short-term memory service pushes a completed conversation batch
to `POST /v1/dream/conversations`. DREAM does not connect to its Redis. Run
queued incremental dreams through `POST /v1/dream/run-pending`; production
deployments also run the same queue automatically from the FastAPI lifespan
worker. The worker discovers active isolated scopes from the event ledger and
runs due AI/User Curators according to their stored interval state.

The current priority artifacts are AI decision cards and isolated `USER.md`
profiles. `POST /v1/dream/run-curators` performs the periodic maintenance dream
for both artifact types. Context returned from `POST /v1/tasks/start` is a
frozen snapshot, so a dream write becomes visible only to the next task.

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
