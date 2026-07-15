from pathlib import Path

from dream.processed_events import ProcessedEventLedger


def test_processed_event_ledger_is_append_only_and_idempotent(
    tmp_path: Path,
) -> None:
    ledger = ProcessedEventLedger(tmp_path / "processed-events.jsonl")
    ledger.append("evt-1")
    ledger.append("evt-1")
    assert ledger.contains("evt-1") is True
    assert ledger.read_all() == ("evt-1",)
