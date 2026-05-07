"""Pytest + jubilant fixtures for testing."""

import subprocess
from pathlib import Path
from typing import Iterator

import jubilant
import pytest

from tests import helpers

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add options."""
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Do not destroy the temporary Juju models created for integration tests.",
    )
    parser.addoption(
        "--sunbeam-model",
        action="store",
        default="sunbeam-controller:admin/openstack-machines",
        help="Existing Juju model to attach the Sunbeam end-to-end suite to.",
    )
    parser.addoption(
        "--sunbeam-app",
        action="store",
        default="microceph",
        help="Application name to target inside the attached Sunbeam model.",
    )


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    """Abort the test session if an abort_on_fail test fails.

    pytest-operator backwd. compat.
    """
    if call.when == "call" and call.excinfo and item.get_closest_marker("abort_on_fail"):
        item.session.shouldstop = "abort_on_fail marker triggered"


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model managed by Jubilant."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 20 * 60
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            if log:
                print(log, end="")


def _build_charm(
    charm_dir: Path,
    *,
    artifact_name: str,
    rebuild: bool = True,
) -> Path:
    """Build a charm at *charm_dir* and return the resulting artifact."""
    artifact = charm_dir / artifact_name
    if not rebuild and artifact.exists():
        return artifact.resolve()

    helpers.ensure_charmcraft()
    subprocess.run(["charmcraft", "-v", "pack"], check=True, cwd=charm_dir)

    # Multi-base charmcraft.yaml emits one artifact per (base, arch); the
    # caller specifies which one it wants by exact filename. Prefer that
    # exact match rather than picking the newest *.charm by mtime, which
    # could grab the wrong base when both jammy and noble are produced.
    if artifact.exists():
        return artifact.resolve()

    built_charms = list(charm_dir.glob("*.charm"))
    if not built_charms:
        raise FileNotFoundError(f"No charm artifacts produced in {charm_dir}")
    raise FileNotFoundError(
        f"Expected charm artifact {artifact_name} not produced in {charm_dir}; "
        f"found: {sorted(c.name for c in built_charms)}"
    )


@pytest.fixture(scope="session")
def microceph_charm() -> Path:
    """Return the built MicroCeph charm artifact."""
    return _build_charm(
        REPO_ROOT,
        artifact_name="microceph_ubuntu-24.04-amd64.charm",
        rebuild=False,
    )
