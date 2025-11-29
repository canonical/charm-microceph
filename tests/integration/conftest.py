"""Pytest + jubilant fixtures for integration testing."""

from pathlib import Path

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
