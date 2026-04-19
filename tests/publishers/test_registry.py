from pathlib import Path
import pytest
from hookbus.publishers.registry import Detector, detect_agents, REGISTRY


def test_detector_absent_binary(tmp_path: Path):
    # agent binary does not exist anywhere -> not detected
    d = Detector(
        agent="fake",
        binary="/tmp/nonexistent-binary-xyz",
        config_home_env="HOME",
        relative_config_path=".fake/openclaw.json",
    )
    assert d.present(home=tmp_path) is False


def test_detector_present_when_binary_exists(tmp_path: Path):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.write_text("#!/bin/sh")
    fake_bin.chmod(0o755)
    d = Detector(
        agent="fake",
        binary=str(fake_bin),
        config_home_env="HOME",
        relative_config_path=".fake/openclaw.json",
    )
    assert d.present(home=tmp_path) is True


def test_registry_has_openclaw_entry():
    agents = [d.agent for d in REGISTRY]
    assert "openclaw" in agents


def test_detect_agents_filters_to_present(tmp_path: Path):
    # only pass one present detector; other is absent
    fake_bin = tmp_path / "fake-bin"
    fake_bin.write_text("x"); fake_bin.chmod(0o755)
    detectors = [
        Detector(agent="present", binary=str(fake_bin),
                 config_home_env="HOME", relative_config_path="x"),
        Detector(agent="absent", binary="/tmp/nonexistent-xyz",
                 config_home_env="HOME", relative_config_path="x"),
    ]
    found = detect_agents(detectors, home=tmp_path)
    assert [d.agent for d in found] == ["present"]


def test_registry_has_claude_code_entry():
    from hookbus.publishers.registry import REGISTRY
    agents = [d.agent for d in REGISTRY]
    assert "claude-code" in agents


def test_claude_code_bundle_files_present():
    from pathlib import Path
    bundle = Path(__file__).resolve().parents[2] / "hookbus" / "publishers" / "bundles" / "claude_code"
    assert (bundle / "hookbus-gate.py").exists()
