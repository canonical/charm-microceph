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

"""Unit tests for shared test helpers."""

from types import SimpleNamespace

import pytest

from tests import helpers


class _FakeJuju:
    def __init__(self, status: SimpleNamespace):
        self._status = status

    def status(self) -> SimpleNamespace:
        return self._status


def _make_status(
    *,
    app_state: str = "active",
    app_message: str = "",
    unit_name: str = "microceph/0",
    workload_state: str = "active",
    workload_message: str = "",
    agent_state: str = "idle",
    agent_message: str = "",
) -> SimpleNamespace:
    unit = SimpleNamespace(
        workload_status=SimpleNamespace(current=workload_state, message=workload_message),
        juju_status=SimpleNamespace(current=agent_state, message=agent_message),
    )
    app = SimpleNamespace(
        app_status=SimpleNamespace(current=app_state, message=app_message),
        units={unit_name: unit},
    )
    return SimpleNamespace(apps={"microceph": app})


def test_microceph_services_snapshot_parses_service_sets() -> None:
    output = """
        Disks: 3
        Services: mon, mgr, mds
        foo: bar
        services: mon, mgr, rgw
    """

    assert helpers.microceph_services_snapshot(output) == [
        {"mon", "mgr", "mds"},
        {"mon", "mgr", "rgw"},
    ]


def test_ceph_status_has_rgw_detects_daemons_dict() -> None:
    status = {
        "servicemap": {
            "services": {
                "rgw": {
                    "daemons": {
                        "rgw.foo": {
                            "metadata": {"id": "foo"},
                        }
                    }
                }
            }
        }
    }

    assert helpers.ceph_status_has_rgw(status) is True


def test_ceph_status_has_rgw_rejects_missing_services() -> None:
    assert helpers.ceph_status_has_rgw({"servicemap": {"services": {}}}) is False


def test_new_unit_names_returns_sorted_difference() -> None:
    assert helpers.new_unit_names(
        {"microceph/2", "microceph/0"},
        {"microceph/0", "microceph/3", "microceph/2"},
    ) == ["microceph/3"]


def test_status_failure_reasons_detects_blocked_app() -> None:
    status = _make_status(app_state="blocked", app_message="storage attach failed")

    assert helpers.status_failure_reasons(status, "microceph") == [
        "app microceph is blocked: storage attach failed"
    ]


def test_status_failure_reasons_detects_unit_error() -> None:
    status = _make_status(
        workload_state="error",
        workload_message="upgrade hook failed",
        agent_state="error",
        agent_message="hook crashed",
    )

    assert helpers.status_failure_reasons(status, "microceph", "microceph/0") == [
        "unit microceph/0 workload is error: upgrade hook failed",
        "unit microceph/0 agent is error: hook crashed",
    ]


def test_wait_for_app_units_fails_fast_on_blocked_app() -> None:
    juju = _FakeJuju(
        _make_status(
            app_state="blocked",
            app_message="refresh failed",
            workload_state="maintenance",
            agent_state="executing",
        )
    )

    with pytest.raises(AssertionError, match="refresh failed"):
        helpers.wait_for_app_units(juju, "microceph", expected_units=2, timeout=30)


def test_wait_for_app_units_prefers_fail_fast_before_success() -> None:
    extra_unit = SimpleNamespace(
        workload_status=SimpleNamespace(current="maintenance", message="waiting"),
        juju_status=SimpleNamespace(current="executing", message="working"),
    )
    status = _make_status(
        app_state="blocked",
        app_message="refresh failed",
        workload_state="maintenance",
        agent_state="executing",
    )
    status.apps["microceph"].units["microceph/1"] = extra_unit
    juju = _FakeJuju(status)

    with pytest.raises(AssertionError, match="refresh failed"):
        helpers.wait_for_app_units(juju, "microceph", expected_units=2, timeout=30)


def test_wait_for_unit_active_idle_fails_fast_on_unit_error() -> None:
    juju = _FakeJuju(
        _make_status(
            workload_state="error",
            workload_message="pebble plan apply failed",
            agent_state="error",
            agent_message="hook failed",
        )
    )

    with pytest.raises(AssertionError, match="pebble plan apply failed"):
        helpers.wait_for_unit_active_idle(juju, "microceph", "microceph/0", timeout=30)
