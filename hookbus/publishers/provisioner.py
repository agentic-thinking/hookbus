"""Provisioner: merge-installs bundled publishers into detected agents.

See publishers/__init__.py for the overall design."""
from __future__ import annotations

import json
from pathlib import Path


class OptOut:
    """Per-host registry of agents the user has told us not to touch."""

    def __init__(self, file_path: Path) -> None:
        self._path = Path(file_path)

    def is_opted_out(self, agent: str) -> bool:
        if not self._path.exists():
            return False
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError:
            return False
        if not isinstance(data, list):
            return False
        return agent in data


def install_file(src, dest, agent: str, bundle_version: str, state) -> None:
    """Copy src -> dest and record in state log.

    If dest already exists and has been user-edited since last install,
    leave it alone (respects user edits). Otherwise copy and record."""
    from pathlib import Path as _P
    src = _P(src)
    dest = _P(dest)
    if dest.exists() and state.has_been_edited(str(dest)):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    from .state import hash_file as _hf
    state.record(
        agent=agent,
        path=str(dest),
        sha256=_hf(src),
        bundle_version=bundle_version,
    )


MARKER = "hookbus_provisioned"


class ConfigCorrupt(Exception):
    """Raised when a target config file is not valid JSON; the provisioner
    refuses to overwrite and leaves the file untouched for user repair."""


def merge_json_config(path, keypath: list[str], entry: dict) -> None:
    """Ensure `entry` is present in the array at `keypath` inside the JSON
    document at `path`. Tags the entry with MARKER so we can find it again.

    - Creates the file if missing.
    - Creates missing intermediate objects and the terminal array if absent.
    - Idempotent: if an entry with MARKER=True already exists in the array,
      does nothing.
    - Corrupt JSON: raises ConfigCorrupt; the file is never overwritten.
    - All other user-owned entries in the array are preserved.
    """
    from pathlib import Path as _P
    p = _P(path)
    if p.exists():
        raw = p.read_text()
        if raw.strip():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ConfigCorrupt(str(e)) from e
        else:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        raise ConfigCorrupt("top level must be an object")

    node = data
    for k in keypath[:-1]:
        if k not in node or not isinstance(node[k], dict):
            node[k] = {}
        node = node[k]
    terminal = keypath[-1]
    if terminal not in node or not isinstance(node[terminal], list):
        node[terminal] = []

    arr = node[terminal]
    for existing in arr:
        if isinstance(existing, dict) and existing.get(MARKER) is True:
            return  # idempotent

    tagged = dict(entry)
    tagged[MARKER] = True
    arr.append(tagged)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def has_existing_gate(path, keypath: list[str], needle: str) -> bool:
    """True if any string value inside the array at `keypath` contains
    `needle`. Used to skip provisioning when the user has already wired
    hookbus-gate by hand."""
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text() or "{}")
    except json.JSONDecodeError:
        return False
    node = data
    for k in keypath:
        if not isinstance(node, dict) or k not in node:
            return False
        node = node[k]
    if not isinstance(node, list):
        return False
    for e in node:
        # flatten any strings we find in the entry
        for v in (e.values() if isinstance(e, dict) else [e]):
            if isinstance(v, str) and needle in v:
                return True
    return False


def uninstall(state) -> None:
    """Reverse every file install recorded in the state log.

    Files the user has edited (hash mismatch or missing) are left alone.
    The state log is always cleared at the end."""
    from pathlib import Path as _P
    for e in state.entries():
        path = _P(e.path)
        if state.has_been_edited(str(path)):
            # user touched it -> do not delete
            pass
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    # clear the log regardless
    for e in list(state.entries()):
        state.remove(e.path)


def provision_agent(agent: str, bundle_dir, install_dest_dir, state, bundle_version: str,
                    skip_files: tuple = ("test_bundle.mjs",)) -> None:
    """Install every file in `bundle_dir` into `install_dest_dir`,
    recording each in the state log. Idempotent (install_file skips
    user-edited files and overwrites the log entry otherwise)."""
    from pathlib import Path as _P
    bundle = _P(bundle_dir)
    dest_root = _P(install_dest_dir)
    for src in bundle.iterdir():
        if not src.is_file() or src.name in skip_files:
            continue
        install_file(
            src=src,
            dest=dest_root / src.name,
            agent=agent,
            bundle_version=bundle_version,
            state=state,
        )
