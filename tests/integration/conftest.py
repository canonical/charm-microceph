"""Pytest + jubilant fixtures for integration testing."""

from pathlib import Path
from typing import Iterator

import jubilant
import pytest

from tests import helpers
from tests.conftest import _build_charm

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())


@pytest.fixture(scope="session")
def cephclient_charm() -> Path:
    """Return the built cephclient tester charm artifact."""
    tester_dir = REPO_ROOT / "tests" / "integration" / "testers" / "cephclient"
    return _build_charm(
        tester_dir,
        artifact_name="cephclient-requirer-mock.charm",
    )


@pytest.fixture(scope="module")
def juju_vm_constraints() -> tuple[str, ...]:
    """Default VM constraints for integration tests that need VM machines."""
    return ("virt-type=virtual-machine", "mem=4G", "root-disk=16G")


@pytest.fixture(scope="module")
def juju_vm(
    request: pytest.FixtureRequest,
    juju_vm_constraints: tuple[str, ...],
) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model configured for VM-based tests."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 60 * 60
        juju.cli("set-model-constraints", *juju_vm_constraints)
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            if log:
                print(log, end="")
