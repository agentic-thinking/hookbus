"""Agent detector registry.

Each Detector describes how to recognise one kind of agent installed on
the host and where its config lives. A provisioner run filters this
registry to the detectors whose binaries are present, then installs
the matching bundle for each."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Detector:
    agent: str
    binary: str
    config_home_env: str
    relative_config_path: str

    def present(self, home: Path | None = None) -> bool:
        p = Path(self.binary)
        if p.is_absolute():
            return p.exists()
        return shutil.which(self.binary) is not None


def detect_agents(detectors: list[Detector], home: Path | None = None) -> list[Detector]:
    return [d for d in detectors if d.present(home=home)]


REGISTRY: list[Detector] = [
    Detector(
        agent="openclaw",
        binary="openclaw",
        config_home_env="HOME",
        relative_config_path=".openclaw/extensions/cre/index.js",
    ),
    Detector(
        agent="claude-code",
        binary="claude",
        config_home_env="HOME",
        relative_config_path=".local/bin/hookbus-gate",
    ),
]
