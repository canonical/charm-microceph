# Copyright 2026 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fixtures for Sunbeam-backed end-to-end tests."""

import jubilant
import pytest

from tests.conftest import DEFAULT_JUJU_BASE, JUJU_BASE_PATTERN


def _sunbeam_juju_base() -> str:
    """Return the fixed base for Sunbeam-backed tests, ignoring ``JUJU_BASE``.

    Sunbeam tests attach to a pre-existing model whose base is fixed by the
    external Sunbeam deployment; they do not create a model, so the base must
    not follow ``JUJU_BASE`` (which only governs tests that create their own
    model). The only consumer of ``juju_base`` in this suite is
    ``microceph_charm``, whose refresh artifact must match the Sunbeam
    model's base.
    """
    return DEFAULT_JUJU_BASE


@pytest.fixture(scope="session")
def juju_base(request: pytest.FixtureRequest) -> str:
    """Match the existing deployment in qualification mode, without changing it."""
    if not getattr(request.config, "_microceph_qualification", None):
        return _sunbeam_juju_base()
    juju = jubilant.Juju(model=str(request.config.getoption("--sunbeam-model")))
    app = str(request.config.getoption("--sunbeam-app"))
    units = juju.status().apps[app].units
    if not units:
        raise pytest.UsageError("Sunbeam has no MicroCeph unit to determine artifact base")
    versions = {
        juju.ssh(unit, "bash", "-c", '. /etc/os-release; printf %s "$VERSION_ID"').strip()
        for unit in units
    }
    if len(versions) != 1:
        raise pytest.UsageError("Sunbeam MicroCeph units have mixed operating-system bases")
    base = "ubuntu@" + versions.pop()
    if not JUJU_BASE_PATTERN.fullmatch(base):
        raise pytest.UsageError(f"Unsupported Sunbeam workload base: {base}")
    return base


@pytest.fixture(scope="session")
def sunbeam_model_name(request: pytest.FixtureRequest) -> str:
    """Return the Juju model name for the Sunbeam deployment under test."""
    return str(request.config.getoption("--sunbeam-model"))


@pytest.fixture(scope="session")
def sunbeam_app_name(request: pytest.FixtureRequest) -> str:
    """Return the MicroCeph application name inside the Sunbeam model."""
    return str(request.config.getoption("--sunbeam-app"))


@pytest.fixture(scope="module")
def sunbeam_juju(
    sunbeam_model_name: str,
    sunbeam_app_name: str,
) -> jubilant.Juju:
    """Attach Jubilant to the Sunbeam-created model and validate the target app."""
    juju = jubilant.Juju(model=sunbeam_model_name, wait_timeout=60 * 60)
    status = juju.status()
    if sunbeam_app_name not in status.apps:
        raise pytest.UsageError(
            f"Application {sunbeam_app_name!r} was not found in model {sunbeam_model_name!r}"
        )
    return juju
