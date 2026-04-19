"""Persistent state log for provisioned publishers.

Records every file the provisioner writes so uninstall can reverse
exactly what was done, and so re-provisioning stays idempotent.

Schema-versioned on disk for future migrations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1


@dataclass
class StateEntry:
    agent: str
    path: str
    sha256: str
    bundle_version: str
    installed_at: str


class StateLog:
    def __init__(self, file_path: Path) -> None:
        self._path = Path(file_path)

    def entries(self) -> list[StateEntry]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text())
        return [StateEntry(**e) for e in data.get("entries", [])]

    def record(
        self,
        agent: str,
        path: str,
        sha256: str,
        bundle_version: str,
    ) -> None:
        entries = self.entries()
        # overwrite any previous entry for the same path
        entries = [e for e in entries if e.path != path]
        entries.append(StateEntry(
            agent=agent,
            path=path,
            sha256=sha256,
            bundle_version=bundle_version,
            installed_at=datetime.now(timezone.utc).isoformat(),
        ))
        self._write(entries)

    def remove(self, path: str) -> None:
        entries = [e for e in self.entries() if e.path != path]
        self._write(entries)


    def has_been_edited(self, path: str) -> bool:
        """True if the file is gone or its hash no longer matches what we recorded."""
        from pathlib import Path as _P
        target = _P(path)
        if not target.exists():
            return True
        for e in self.entries():
            if e.path == path:
                return hash_file(target) != e.sha256
        # not recorded -> treat as edited (we did not own it)
        return True
    def _write(self, entries: list[StateEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": SCHEMA, "entries": [asdict(e) for e in entries]}
        self._path.write_text(json.dumps(payload, indent=2))



def hash_file(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
