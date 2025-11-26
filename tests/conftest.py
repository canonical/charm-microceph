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

    subprocess.run(["charmcraft", "-v", "pack"], check=True, cwd=charm_dir)
    built_charms = sorted(
        charm_dir.glob("*.charm"), key=lambda charm: charm.stat().st_mtime, reverse=True
    )
    if not built_charms:
        raise FileNotFoundError(f"No charm artifacts produced in {charm_dir}")

    latest_artifact = built_charms[0]
    if latest_artifact != artifact:
        latest_artifact.rename(artifact)

    if artifact.exists():
        return artifact.resolve()
    raise FileNotFoundError(f"Expected charm artifact {artifact} was not produced")


@pytest.fixture(scope="session")
def microceph_charm() -> Path:
    """Return the built MicroCeph charm artifact."""
    return _build_charm(
        REPO_ROOT,
        artifact_name="microceph.charm",
        rebuild=False,
    )
