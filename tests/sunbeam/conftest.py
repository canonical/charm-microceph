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
