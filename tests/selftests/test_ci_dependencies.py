# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Exercise dependency bootstrap without sudo, package installs or a controller."""

import os
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "scripts" / "ci_helpers.sh"


def _install_deps(tmp_path, failing_command=""):
    """Run the actual shell helper with harmless command stand-ins."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands"
    for name in ("sudo", "juju"):
        executable = bin_dir / name
        executable.write_text(
            "#!/bin/bash\n"
            'command="$(basename "$0") $*"\n'
            'printf "%s\\n" "$command" >> "$COMMAND_LOG"\n'
            'if [[ "$command" == "$FAILING_COMMAND" ]]; then exit 42; fi\n'
        )
        executable.chmod(0o755)
    result = subprocess.run(
        ["bash", str(HELPER), "install_deps"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path / "home"),
            "COMMAND_LOG": str(log),
            "FAILING_COMMAND": failing_command,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, log.read_text().splitlines()


def test_refreshes_package_indexes_before_installing_tox(tmp_path):
    """Fresh runners must populate indexes before installing dependency packages."""
    result, commands = _install_deps(tmp_path)
    assert result.returncode == 0, result.stderr
    assert commands.index("sudo apt-get -qq update") < commands.index(
        "sudo apt-get -qq install jq tox"
    )
    assert commands.index("sudo apt-get -qq install jq tox") < commands.index(
        "juju bootstrap localhost lxd"
    )


@pytest.mark.parametrize("failed", ["sudo apt-get -qq update", "sudo apt-get -qq install jq tox"])
def test_failed_dependency_setup_stops_before_controller_bootstrap(tmp_path, failed):
    """Report the dependency failure rather than continuing with missing tooling."""
    result, commands = _install_deps(tmp_path, failed)
    assert result.returncode == 42
    assert commands[-1] == failed
    assert not any(command.startswith("juju ") for command in commands)
