import pytest
from pathlib import Path
import json
from hookbus.publishers.provisioner import OptOut


def test_optout_empty_when_missing(tmp_path: Path):
    o = OptOut(tmp_path / "opt-out.json")
    assert o.is_opted_out("openclaw") is False


def test_optout_respects_list(tmp_path: Path):
    f = tmp_path / "opt-out.json"
    f.write_text(json.dumps(["openclaw"]))
    o = OptOut(f)
    assert o.is_opted_out("openclaw") is True
    assert o.is_opted_out("claude-code") is False


def test_optout_malformed_defaults_safe(tmp_path: Path):
    f = tmp_path / "opt-out.json"
    f.write_text("not-json")
    o = OptOut(f)
    # corrupt file: default to not-opted-out (do not silently block provisioning)
    assert o.is_opted_out("openclaw") is False


from hookbus.publishers.provisioner import install_file
from hookbus.publishers.state import StateLog, hash_file


def test_install_file_copies_and_records(tmp_path: Path):
    src = tmp_path / "bundle" / "index.js"
    src.parent.mkdir()
    src.write_text("// bundled publisher")
    dest = tmp_path / "home" / ".openclaw" / "extensions" / "cre" / "index.js"
    log = StateLog(tmp_path / "state.json")

    install_file(src, dest, agent="openclaw", bundle_version="0.1.0", state=log)

    assert dest.exists()
    assert dest.read_text() == "// bundled publisher"
    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].agent == "openclaw"
    assert entries[0].path == str(dest)
    assert entries[0].sha256 == hash_file(src)
    assert entries[0].bundle_version == "0.1.0"


def test_install_file_creates_parent_dirs(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    dest = tmp_path / "deep" / "nested" / "a.txt"
    log = StateLog(tmp_path / "state.json")
    install_file(src, dest, agent="x", bundle_version="1", state=log)
    assert dest.exists()


def test_install_file_skips_if_user_edited(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("v1")
    dest = tmp_path / "dest.txt"
    log = StateLog(tmp_path / "state.json")

    install_file(src, dest, agent="x", bundle_version="1", state=log)
    dest.write_text("USER TAMPERED")  # user edits the installed file
    install_file(src, dest, agent="x", bundle_version="2", state=log)  # retry

    assert dest.read_text() == "USER TAMPERED"  # install must not clobber user


from hookbus.publishers.provisioner import merge_json_config, MARKER


def test_merge_append_to_existing_array(tmp_path: Path):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"command": "user-owned-hook"}]}
    }))
    entry = {"command": "hookbus-gate"}
    merge_json_config(cfg, keypath=["hooks", "PreToolUse"], entry=entry)
    data = json.loads(cfg.read_text())
    arr = data["hooks"]["PreToolUse"]
    assert len(arr) == 2
    assert arr[0] == {"command": "user-owned-hook"}  # preserved
    assert arr[1]["command"] == "hookbus-gate"
    assert arr[1][MARKER] is True


def test_merge_idempotent(tmp_path: Path):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"hooks": {"PreToolUse": []}}))
    entry = {"command": "hookbus-gate"}
    merge_json_config(cfg, keypath=["hooks", "PreToolUse"], entry=entry)
    merge_json_config(cfg, keypath=["hooks", "PreToolUse"], entry=entry)
    arr = json.loads(cfg.read_text())["hooks"]["PreToolUse"]
    assert len(arr) == 1  # not duplicated


def test_merge_creates_missing_array(tmp_path: Path):
    # tweak 2: no PreToolUse key at all -> create it
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"some": "other"}))
    entry = {"command": "hookbus-gate"}
    merge_json_config(cfg, keypath=["hooks", "PreToolUse"], entry=entry)
    data = json.loads(cfg.read_text())
    assert data["hooks"]["PreToolUse"][0]["command"] == "hookbus-gate"
    assert data["some"] == "other"  # unrelated keys untouched


def test_merge_creates_missing_file(tmp_path: Path):
    cfg = tmp_path / "settings.json"
    entry = {"command": "hookbus-gate"}
    merge_json_config(cfg, keypath=["hooks", "PreToolUse"], entry=entry)
    assert cfg.exists()
    arr = json.loads(cfg.read_text())["hooks"]["PreToolUse"]
    assert arr[0]["command"] == "hookbus-gate"


def test_merge_corrupt_file_raises_skip(tmp_path: Path):
    # tweak 2: corrupt JSON should raise ConfigCorrupt, not crash or clobber
    from hookbus.publishers.provisioner import ConfigCorrupt
    cfg = tmp_path / "settings.json"
    cfg.write_text("{ this is not json")
    with pytest.raises(ConfigCorrupt):
        merge_json_config(cfg, keypath=["hooks", "PreToolUse"], entry={"command": "x"})
    # file must be untouched
    assert cfg.read_text() == "{ this is not json"


from hookbus.publishers.provisioner import has_existing_gate, uninstall


def test_detect_existing_hand_rolled_gate(tmp_path: Path):
    # a user already wired hookbus-gate by hand (no MARKER)
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"command": "/usr/local/bin/hookbus-gate"}]}
    }))
    assert has_existing_gate(cfg, keypath=["hooks", "PreToolUse"], needle="hookbus-gate") is True


def test_detect_absent_gate(tmp_path: Path):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"hooks": {"PreToolUse": [{"command": "/tmp/other"}]}}))
    assert has_existing_gate(cfg, keypath=["hooks", "PreToolUse"], needle="hookbus-gate") is False


def test_detect_missing_file(tmp_path: Path):
    assert has_existing_gate(tmp_path / "none.json", keypath=["hooks", "PreToolUse"], needle="x") is False


def test_uninstall_removes_recorded_files_only(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("x")
    dest_a = tmp_path / "a.txt"
    dest_b = tmp_path / "b.txt"
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("user file")

    log = StateLog(tmp_path / "state.json")
    install_file(src, dest_a, agent="openclaw", bundle_version="1", state=log)
    install_file(src, dest_b, agent="openclaw", bundle_version="1", state=log)

    uninstall(log)

    assert not dest_a.exists()
    assert not dest_b.exists()
    assert unrelated.exists()  # untouched
    assert log.entries() == []


def test_uninstall_leaves_user_edited_files(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("x")
    dest = tmp_path / "edited.txt"
    log = StateLog(tmp_path / "state.json")
    install_file(src, dest, agent="openclaw", bundle_version="1", state=log)
    dest.write_text("USER EDIT")  # tamper

    uninstall(log)

    assert dest.exists()
    assert dest.read_text() == "USER EDIT"  # preserved
    assert log.entries() == []  # recorded entries purged either way


from hookbus.publishers.provisioner import provision_agent


def test_provision_openclaw_end_to_end(tmp_path: Path):
    # simulate an openclaw install root
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".openclaw" / "extensions").mkdir(parents=True)

    # pick an existing bundle dir from the repo (we are testing plumbing,
    # not re-verifying the bundle contents)
    repo_bundle = Path(__file__).resolve().parents[2] / "hookbus" / "publishers" / "bundles" / "openclaw"
    assert (repo_bundle / "index.js").exists()

    state = StateLog(tmp_path / "state.json")
    provision_agent(
        agent="openclaw",
        bundle_dir=repo_bundle,
        install_dest_dir=fake_home / ".openclaw" / "extensions" / "cre",
        state=state,
        bundle_version="0.2.0",
    )

    # all three bundle files installed
    for fn in ["index.js", "openclaw.plugin.json", "package.json"]:
        assert (fake_home / ".openclaw" / "extensions" / "cre" / fn).exists()
    # state recorded
    assert {e.path.split("/")[-1] for e in state.entries()} >= {"index.js", "openclaw.plugin.json", "package.json"}

    # idempotent: running again does not error and does not duplicate
    n_before = len(state.entries())
    provision_agent(
        agent="openclaw", bundle_dir=repo_bundle,
        install_dest_dir=fake_home / ".openclaw" / "extensions" / "cre",
        state=state, bundle_version="0.2.0",
    )
    assert len(state.entries()) == n_before
