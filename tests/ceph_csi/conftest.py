# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Pytest + jubilant fixtures for ceph-csi integration testing."""

import os
from pathlib import Path
from typing import Iterator

import jubilant
import pytest

from tests import helpers
from tests.conftest import _build_charm

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())


CEPH_CSI_CHANNEL = "latest/edge"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add CLI options for ceph-csi tests.

    Note: --keep-models and abort_on_fail are already registered by the root tests/conftest.py.
    """
    parser.addoption(
        "--ceph-csi-charm",
        action="store",
        default=None,
        help="Path to a pre-built ceph-csi charm artifact (overrides charmhub).",
    )
    parser.addoption(
        "--ceph-csi-channel",
        action="store",
        default=None,
        help=f"Charmhub channel for ceph-csi (default: {CEPH_CSI_CHANNEL}).",
    )


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model for microceph (machine model)."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 20 * 60
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            if log:
                print(log, end="")


@pytest.fixture(scope="module")
def k8s_juju(request: pytest.FixtureRequest) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model for k8s + ceph-csi (machine model)."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as juju:
        juju.wait_timeout = 30 * 60
        yield juju
        if request.session.testsfailed:
            log = juju.debug_log(limit=1000)
            if log:
                print(log, end="")


@pytest.fixture(scope="session")
def microceph_charm() -> Path:
    """Return the built MicroCeph charm artifact."""
    return _build_charm(
        REPO_ROOT,
        artifact_name="microceph.charm",
        rebuild=False,
    )


@pytest.fixture(scope="session")
def ceph_csi_source(request: pytest.FixtureRequest) -> dict:
    """Return ceph-csi deploy source: either a local path or charmhub channel.

    Returns a dict with either {"charm": Path} or {"channel": str}.
    Priority: --ceph-csi-charm > CEPH_CSI_CHARM env > --ceph-csi-channel > default channel.
    """
    charm_path = request.config.getoption("--ceph-csi-charm") or os.environ.get(
        "CEPH_CSI_CHARM"
    )
    if charm_path:
        path = Path(charm_path).resolve()
        if not path.exists():
            pytest.fail(f"ceph-csi charm not found at {path}")
        return {"charm": path}

    channel = (
        request.config.getoption("--ceph-csi-channel")
        or os.environ.get("CEPH_CSI_CHANNEL")
        or CEPH_CSI_CHANNEL
    )
    return {"channel": channel}
