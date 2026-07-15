"""Durable record of completed Background Review source events."""

import json
import os
from pathlib import Path
from threading import RLock


class ProcessedEventLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def read_all(self) -> tuple[str, ...]:
        with self._lock:
            if not self.path.exists():
                return ()
            values: list[str] = []
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
                handle.write(
                    json.dumps(
                        {"event_id": event_id},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
