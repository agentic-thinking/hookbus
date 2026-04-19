"""bus.main() wires provisioning behind a --no-provision flag."""
from unittest.mock import patch
import pytest

from hookbus import bus as bus_mod


def test_main_noprovision_flag_skips_provisioner():
    with patch.object(bus_mod, "_run_provisioner") as mock_prov, \
         patch.object(bus_mod, "_run_server") as mock_run:
        bus_mod.main(argv=["--no-provision"])
        mock_prov.assert_not_called()
        mock_run.assert_called_once()


def test_main_default_calls_provisioner():
    with patch.object(bus_mod, "_run_provisioner") as mock_prov, \
         patch.object(bus_mod, "_run_server") as mock_run:
        bus_mod.main(argv=[])
        mock_prov.assert_called_once()
        mock_run.assert_called_once()
