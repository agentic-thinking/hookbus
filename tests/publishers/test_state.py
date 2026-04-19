"""
TDD: StateLog should persist and reload install records with schema versioning.
"""
import json
from pathlib import Path

import pytest

from hookbus.publishers.state import StateLog


def test_state_log_roundtrip(tmp_path: Path):
    log_path = tmp_path / "provisioned.json"
    log = StateLog(log_path)

    log.record(
        agent="openclaw",
        path="/home/x/.openclaw/extensions/cre/index.js",
        sha256="abc123",
        bundle_version="0.1.0",
    )

    reloaded = StateLog(log_path)
    entries = reloaded.entries()
    assert len(entries) == 1
    assert entries[0].agent == "openclaw"
    assert entries[0].sha256 == "abc123"
    # schema must be persisted (tweak 1: schema versioning)
    with open(log_path) as f:
        data = json.load(f)
    assert data["schema"] == 1
    assert "entries" in data


def test_state_log_missing_file(tmp_path: Path):
    log = StateLog(tmp_path / "nonexistent.json")
    assert log.entries() == []


def test_state_log_remove(tmp_path: Path):
    log_path = tmp_path / "provisioned.json"
    log = StateLog(log_path)
    log.record(agent="a", path="/tmp/x", sha256="s", bundle_version="v")
    log.record(agent="b", path="/tmp/y", sha256="s", bundle_version="v")
    log.remove(path="/tmp/x")
    entries = StateLog(log_path).entries()
    assert len(entries) == 1
    assert entries[0].agent == "b"


def test_hash_file(tmp_path: Path):
    from hookbus.publishers.state import hash_file
    f = tmp_path / "x.txt"
    f.write_text("hello")
    h = hash_file(f)
    assert len(h) == 64  # sha256 hex
    assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_has_been_edited_true(tmp_path: Path):
    from hookbus.publishers.state import hash_file
    log_path = tmp_path / "provisioned.json"
    log = StateLog(log_path)
    target = tmp_path / "plugin.js"
    target.write_text("original")
    log.record(agent="a", path=str(target), sha256=hash_file(target), bundle_version="v1")
    # user edits the file
    target.write_text("user tampered")
    assert log.has_been_edited(str(target)) is True


def test_has_been_edited_false_when_unchanged(tmp_path: Path):
    from hookbus.publishers.state import hash_file
    log_path = tmp_path / "provisioned.json"
    log = StateLog(log_path)
    target = tmp_path / "plugin.js"
    target.write_text("original")
    log.record(agent="a", path=str(target), sha256=hash_file(target), bundle_version="v1")
    assert log.has_been_edited(str(target)) is False


def test_has_been_edited_missing_returns_true(tmp_path: Path):
    # file recorded but deleted -> treat as edited so we do not reprovision over absent
    log_path = tmp_path / "provisioned.json"
    log = StateLog(log_path)
    target = tmp_path / "plugin.js"
    target.write_text("x")
    log.record(agent="a", path=str(target), sha256="deadbeef" * 8, bundle_version="v1")
    target.unlink()
    assert log.has_been_edited(str(target)) is True
