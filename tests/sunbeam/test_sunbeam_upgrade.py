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

"""Sunbeam-backed end-to-end upgrade validation."""

from dataclasses import dataclass, field
from pathlib import Path

import jubilant
import pytest

from tests import helpers

DEFAULT_TIMEOUT = 1800
pytestmark = [pytest.mark.sunbeam, pytest.mark.slow]


@dataclass
class SunbeamScenarioState:
    """Mutable state shared across the incremental Sunbeam scenario."""

    initial_units: set[str] = field(default_factory=set)
    added_unit: str | None = None
    target_unit: str | None = None


@pytest.fixture(scope="module")
def sunbeam_state() -> SunbeamScenarioState:
    """Track the unit selected for the Sunbeam scenario."""
    return SunbeamScenarioState()


@pytest.mark.incremental
class TestSunbeamUpgrade:
    @pytest.mark.abort_on_fail
    def test_baseline_sanity(
        self,
        sunbeam_juju: jubilant.Juju,
        sunbeam_app_name: str,
        sunbeam_state: SunbeamScenarioState,
    ) -> None:
        """Validate that the target application exists before mutating it."""
        status = sunbeam_juju.status()
        app = status.apps.get(sunbeam_app_name)
        if not app:
            raise AssertionError(f"Application {sunbeam_app_name!r} not found in Sunbeam model")

        sunbeam_state.initial_units = set(app.units)
        if app.units:
            helpers.wait_for_unit_active_idle(
                sunbeam_juju,
                sunbeam_app_name,
                helpers.first_unit_name(status, sunbeam_app_name),
                timeout=DEFAULT_TIMEOUT,
            )

    @pytest.mark.abort_on_fail
    def test_refresh_and_add_unit(
        self,
        sunbeam_juju: jubilant.Juju,
        sunbeam_app_name: str,
        sunbeam_state: SunbeamScenarioState,
        microceph_charm: Path,
    ) -> None:
        """Refresh the app, validate the new unit, and then settle ``microceph/0``."""
        sunbeam_juju.cli("refresh", sunbeam_app_name, "--path", str(microceph_charm))
        sunbeam_juju.cli("add-unit", sunbeam_app_name, "--to", "0")

        status = helpers.wait_for_app_units(
            sunbeam_juju,
            sunbeam_app_name,
            expected_units=len(sunbeam_state.initial_units) + 1,
            timeout=DEFAULT_TIMEOUT,
        )
        current_units = set(status.apps[sunbeam_app_name].units)
        new_units = helpers.new_unit_names(sunbeam_state.initial_units, current_units)
        if len(new_units) != 1:
            raise AssertionError(
                "Expected exactly one new unit after `juju add-unit`; "
                f"initial units: {sorted(sunbeam_state.initial_units)}, current units: {sorted(current_units)}"
            )

        sunbeam_state.added_unit = new_units[0]
        helpers.wait_for_unit_active_idle(
            sunbeam_juju,
            sunbeam_app_name,
            sunbeam_state.added_unit,
            timeout=DEFAULT_TIMEOUT,
        )

        # Mirror the workflow exactly: Sunbeam creates the model/application and
        # the test refreshes the existing app, then mutates ``microceph/0``.
        sunbeam_state.target_unit = f"{sunbeam_app_name}/0"
        if sunbeam_state.target_unit not in current_units:
            raise AssertionError(
                f"Expected workflow target {sunbeam_state.target_unit} to exist after add-unit; "
                f"current units: {sorted(current_units)}"
            )
        if sunbeam_state.target_unit != sunbeam_state.added_unit:
            helpers.wait_for_unit_active_idle(
                sunbeam_juju,
                sunbeam_app_name,
                sunbeam_state.target_unit,
                timeout=DEFAULT_TIMEOUT,
            )

    @pytest.mark.abort_on_fail
    def test_add_storage_and_wait_for_osds(
        self,
        sunbeam_juju: jubilant.Juju,
        sunbeam_app_name: str,
        sunbeam_state: SunbeamScenarioState,
    ) -> None:
        """Add loop-backed storage and mirror the workflow's OSD-only readiness gate."""
        if not sunbeam_state.target_unit:
            raise AssertionError("Target unit was not selected by the refresh/add-unit step")

        sunbeam_juju.cli("add-storage", sunbeam_state.target_unit, "osd-standalone=loop,2G,3")
        helpers.assert_osd_count(
            sunbeam_juju,
            sunbeam_app_name,
            expected_osds=3,
            timeout=DEFAULT_TIMEOUT,
        )
        helpers.enable_missing_pool_apps(
            sunbeam_juju,
            sunbeam_app_name,
            unit_name=sunbeam_state.target_unit,
        )

    @pytest.mark.abort_on_fail
    def test_enable_rgw_and_exercise_object_io(
        self,
        sunbeam_juju: jubilant.Juju,
        sunbeam_app_name: str,
        sunbeam_state: SunbeamScenarioState,
    ) -> None:
        """Enable RGW and validate unit-local health plus object IO."""
        if not sunbeam_state.target_unit:
            raise AssertionError("Target unit was not selected by the refresh/add-unit step")

        sunbeam_juju.config(sunbeam_app_name, {"enable-rgw": "*"})
        helpers.wait_for_unit_active_idle(
            sunbeam_juju,
            sunbeam_app_name,
            sunbeam_state.target_unit,
            timeout=DEFAULT_TIMEOUT,
        )
        helpers.wait_for_microceph_status_rgw(
            sunbeam_juju,
            sunbeam_state.target_unit,
            expected_nodes=1,
            timeout=DEFAULT_TIMEOUT,
        )
        rgw_status = helpers.wait_for_ceph_status_rgw(
            sunbeam_juju,
            sunbeam_app_name,
            timeout=DEFAULT_TIMEOUT,
        )
        assert helpers.ceph_status_has_rgw(rgw_status)
        helpers.assert_rgw_healthcheck(
            sunbeam_juju,
            sunbeam_state.target_unit,
            timeout=DEFAULT_TIMEOUT,
        )
        helpers.exercise_rgw(sunbeam_juju, sunbeam_state.target_unit)
