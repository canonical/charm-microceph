# Copyright 2026 Canonical Ltd.
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

"""Integration test for issue #182 update-status recovery."""

import logging
import time
from pathlib import Path

import jubilant
import pytest

import cluster
from tests import helpers

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.slow

APP_NAME = "microceph"
LOOP_OSD_SPEC = "1G,3"
DEFAULT_TIMEOUT = 2400
ROOT_DISK_CONSTRAINT = "root-disk=24G"


@pytest.fixture(scope="module")
def juju_vm_constraints() -> tuple[str, ...]:
    """Override VM constraints for this module's larger root disk requirement."""
    return ("virt-type=virtual-machine", "mem=4G", ROOT_DISK_CONSTRAINT)


@pytest.fixture(scope="module")
def juju(juju_vm: jubilant.Juju) -> jubilant.Juju:
    """Alias VM-backed Juju model fixture for this test module."""
    return juju_vm


@pytest.fixture(scope="module")
def deployed_microceph(juju: jubilant.Juju, microceph_charm: Path) -> str:
    """Deploy microceph with loop OSDs and wait for the app to settle."""
    helpers.deploy_microceph(
        juju,
        microceph_charm,
        APP_NAME,
        loop_osd_spec=LOOP_OSD_SPEC,
        timeout=DEFAULT_TIMEOUT,
    )
    _wait_for_ceph_status_available(juju, APP_NAME, timeout=DEFAULT_TIMEOUT)

    unit_name = helpers.first_unit_name(juju.status(), APP_NAME)
    helpers.enable_missing_pool_apps(juju, APP_NAME, unit_name=unit_name)
    helpers.wait_for_ceph_health_ok(juju, APP_NAME, timeout=DEFAULT_TIMEOUT)
    return APP_NAME


def _pick_same_track_upgrade_channel(
    juju: jubilant.Juju, unit_name: str, current_channel: str
) -> str | None:
    """Pick an alternate risk on the same track from snap info."""
    track = current_channel.split("/")[0]
    output = juju.ssh(unit_name, "snap", "info", "microceph")

    for risk in ("candidate", "beta", "edge"):
        candidate = f"{track}/{risk}"
        if candidate == current_channel:
            continue
        if f"{candidate}:" in output:
            return candidate

    return None


def _wait_for_ceph_status_available(
    juju: jubilant.Juju, app: str, timeout: int = DEFAULT_TIMEOUT
) -> dict:
    """Wait until ceph status can be fetched successfully."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return helpers.fetch_ceph_status(juju, app)
        except Exception:
            time.sleep(10)

    raise AssertionError("Timed out waiting for ceph status output")


def _noout_flag_present(status: dict) -> bool:
    """Return whether the noout flag is present in ceph health checks."""
    checks = status.get("health", {}).get("checks", {})
    osdmap_flags = checks.get("OSDMAP_FLAGS")
    if not isinstance(osdmap_flags, dict):
        return False

    summary = osdmap_flags.get("summary", {}).get("message", "")
    details = " ".join(item.get("message", "") for item in osdmap_flags.get("detail", []))
    return "noout" in f"{summary} {details}".lower()


def _wait_for_noout_flag(
    juju: jubilant.Juju,
    app: str,
    *,
    expected: bool,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Wait until the noout OSDMAP flag appears/disappears in ceph health checks."""
    deadline = time.time() + timeout
    last_status = {}
    while time.time() < deadline:
        last_status = helpers.fetch_ceph_status(juju, app)
        if _noout_flag_present(last_status) is expected:
            return last_status
        time.sleep(10)

    state = "present" if expected else "absent"
    raise AssertionError(
        f"Timed out waiting for noout flag to be {state}; last status: {last_status}"
    )


def _assert_app_message_contains(juju: jubilant.Juju, app: str, expected: str) -> None:
    status = juju.status()
    app_status = status.apps.get(app)
    if not app_status:
        raise AssertionError(f"App {app} not found in status")

    msg = app_status.app_status.message
    if expected not in msg:
        raise AssertionError(f"Expected app status message to contain {expected!r}, got: {msg!r}")


@pytest.mark.abort_on_fail
def test_upgrade_health_warning_recovers_on_update_status(
    juju: jubilant.Juju,
    deployed_microceph: str,
) -> None:
    """Verify blocked status from transient Ceph health warning auto-recovers."""
    status = juju.status()
    unit_name = helpers.first_unit_name(status, APP_NAME)

    current_channel = str(juju.config(APP_NAME).get("snap-channel", ""))
    if not current_channel:
        pytest.skip("snap-channel config is unavailable")

    target_channel = _pick_same_track_upgrade_channel(juju, unit_name, current_channel)
    if not target_channel:
        pytest.skip(
            f"No alternate same-track channel found for current channel {current_channel!r}"
        )

    logger.info("Using channel transition %s -> %s", current_channel, target_channel)

    # Ensure the test starts from no noout flag, then inject a transient health warning.
    juju.ssh(unit_name, "sudo", "ceph", "osd", "unset", "noout")
    _wait_for_noout_flag(juju, APP_NAME, expected=False, timeout=600)

    juju.ssh(unit_name, "sudo", "ceph", "osd", "set", "noout")
    try:
        _wait_for_noout_flag(juju, APP_NAME, expected=True, timeout=600)

        juju.config(APP_NAME, {"snap-channel": target_channel})

        with helpers.fast_forward(juju):
            helpers.wait_for_status(juju, APP_NAME, ("blocked",), timeout=600)

        _assert_app_message_contains(juju, APP_NAME, cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX)
    finally:
        juju.ssh(unit_name, "sudo", "ceph", "osd", "unset", "noout")

    _wait_for_noout_flag(juju, APP_NAME, expected=False, timeout=DEFAULT_TIMEOUT)

    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=DEFAULT_TIMEOUT)
