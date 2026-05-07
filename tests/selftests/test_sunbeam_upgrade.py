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

"""Unit tests for the Sunbeam end-to-end scenario wiring."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import helpers
from tests.sunbeam import test_sunbeam_upgrade as sunbeam_upgrade


class _FakeJuju:
    def __init__(self):
        self.cli_calls: list[tuple[str, ...]] = []

    def cli(self, *args: str) -> None:
        self.cli_calls.append(args)


def _make_status(unit_names: set[str]) -> SimpleNamespace:
    units = {
        unit_name: SimpleNamespace(
            workload_status=SimpleNamespace(current="active", message=""),
            juju_status=SimpleNamespace(current="idle", message=""),
        )
        for unit_name in unit_names
    }
    app = SimpleNamespace(
        app_status=SimpleNamespace(current="active", message=""),
        units=units,
    )
    return SimpleNamespace(apps={"microceph": app})


def test_refresh_and_add_unit_validates_new_unit_before_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = sunbeam_upgrade.SunbeamScenarioState(initial_units={"microceph/0"})
    juju = _FakeJuju()
    status = _make_status({"microceph/0", "microceph/1"})
    waited_units: list[str] = []

    monkeypatch.setattr(helpers, "wait_for_app_units", lambda *args, **kwargs: status)
    monkeypatch.setattr(
        helpers,
        "wait_for_unit_active_idle",
        lambda _juju, _app, unit_name, **kwargs: waited_units.append(unit_name),
    )

    sunbeam_upgrade.TestSunbeamUpgrade().test_refresh_and_add_unit(
        juju, "microceph", state, Path("microceph_ubuntu-24.04-amd64.charm")
    )

    assert juju.cli_calls == [
        ("refresh", "microceph", "--path", "microceph_ubuntu-24.04-amd64.charm"),
        ("add-unit", "microceph", "--to", "0"),
    ]
    assert state.added_unit == "microceph/1"
    assert state.target_unit == "microceph/0"
    assert waited_units == ["microceph/1", "microceph/0"]


def test_refresh_and_add_unit_fails_when_workflow_target_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = sunbeam_upgrade.SunbeamScenarioState(initial_units={"microceph/1"})
    juju = _FakeJuju()
    status = _make_status({"microceph/1", "microceph/2"})
    waited_units: list[str] = []

    monkeypatch.setattr(helpers, "wait_for_app_units", lambda *args, **kwargs: status)
    monkeypatch.setattr(
        helpers,
        "wait_for_unit_active_idle",
        lambda _juju, _app, unit_name, **kwargs: waited_units.append(unit_name),
    )

    with pytest.raises(AssertionError, match="Expected workflow target microceph/0 to exist"):
        sunbeam_upgrade.TestSunbeamUpgrade().test_refresh_and_add_unit(
            juju,
            "microceph",
            state,
            Path("microceph_ubuntu-24.04-amd64.charm"),
        )

    assert state.added_unit == "microceph/2"
    assert state.target_unit == "microceph/0"
    assert waited_units == ["microceph/2"]
