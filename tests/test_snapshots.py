from pathlib import Path

import pytest

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


@pytest.mark.parametrize("path", [Path("/tmp/escape"), Path("../escape")])
def test_artifact_store_rejects_paths_outside_scope(tmp_path: Path, path: Path) -> None:
    store = AtomicArtifactStore(tmp_path / "agent")
    with pytest.raises(ValueError):
        store.write_text(path, "blocked")
