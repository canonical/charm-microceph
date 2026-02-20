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

"""Integration tests for osd-devices config option (CE127 Phase 1).

These tests verify that the charm correctly processes the osd-devices
configuration option, which allows declarative device selection via
a DSL expression.

All tests in this module run in VMs with LXD block devices attached
for OSD enrollment testing.
"""

import json
import logging
from pathlib import Path
from typing import Iterator

import jubilant
import pytest

from tests import helpers
from tests.helpers import lxd

logger = logging.getLogger(__name__)

APP_NAME = "microceph"


def _wait_for_active(juju_vm: jubilant.Juju, timeout: int = 300) -> None:
    """Wait for app to reach active/idle."""
    with helpers.fast_forward(juju_vm):
        helpers.wait_for_apps(juju_vm, APP_NAME, timeout=timeout)


def _set_osd_config(
    juju_vm: jubilant.Juju,
    osd_devices: str,
    device_add_flags: str = "",
) -> None:
    """Set osd-devices related config options."""
    juju_vm.config(
        APP_NAME,
        {
            "osd-devices": osd_devices,
            "device-add-flags": device_add_flags,
        },
    )


def _apply_osd_config_and_wait(
    juju_vm: jubilant.Juju,
    osd_devices: str,
    device_add_flags: str = "",
    timeout: int = 300,
) -> None:
    """Apply osd-devices config and wait for active status."""
    _set_osd_config(juju_vm, osd_devices=osd_devices, device_add_flags=device_add_flags)
    _wait_for_active(juju_vm, timeout=timeout)


def _clear_osd_config_and_wait(juju_vm: jubilant.Juju, timeout: int = 300) -> None:
    """Clear osd-devices related config and wait for active status."""
    _set_osd_config(juju_vm, osd_devices="", device_add_flags="")
    _wait_for_active(juju_vm, timeout=timeout)


@pytest.fixture(scope="module")
def deployed_microceph(juju_vm: jubilant.Juju, microceph_charm: Path):
    """Deploy MicroCeph in a VM for config testing."""
    logger.info("Deploying MicroCeph (VM)")
    helpers.deploy_microceph(
        juju_vm,
        microceph_charm,
        APP_NAME,
        config={"snap-channel": "latest/edge"},
        timeout=3600,
    )
    return APP_NAME


@pytest.fixture(scope="module")
def attached_block_device(juju_vm: jubilant.Juju, deployed_microceph) -> Iterator[str]:
    """Create and attach an LXD block volume to the deployed VM.

    The volume must be >2GB as MicroCeph's AvailableDisks ignores devices
    smaller than MinOSDSize (2,147,483,648 bytes / 2GB).

    Yields the disk path inside the VM. Cleaned up after the module.
    """
    unit_name = helpers.first_unit_name(juju_vm.status(), APP_NAME)
    inst_id = lxd.get_instance_id(juju_vm, APP_NAME, unit_name)
    logger.info(f"Instance ID: {inst_id}")

    pool = lxd.get_lxd_storage_pool()
    logger.info(f"Using LXD storage pool: {pool}")

    model_name = juju_vm.status().model.name
    vol_name = f"osd-cfg-test-{model_name}"

    lxd.create_and_attach_volume(pool, vol_name, inst_id, size="3GB")
    try:
        disk_path = lxd.wait_for_disk(juju_vm, unit_name, size_pattern="3G|2.8G|2.9G")
        logger.info(f"Block device available at {disk_path}")
        yield disk_path
    finally:
        lxd.cleanup_volume(pool, vol_name, inst_id)


@pytest.mark.abort_on_fail
def test_osd_devices_config_invalid_flags(juju_vm: jubilant.Juju, deployed_microceph):
    """Verify that invalid device-add-flags cause a blocked status."""
    logger.info("Testing invalid device-add-flags detection")

    # Set an invalid flag
    juju_vm.config(
        APP_NAME,
        {
            "osd-devices": "eq(@type,'disk')",
            "device-add-flags": "invalid:flag",
        },
    )

    with helpers.fast_forward(juju_vm):
        helpers.wait_for_status(juju_vm, APP_NAME, ("blocked", "error"), timeout=120)

    # Reset config to unblock
    _set_osd_config(juju_vm, osd_devices="", device_add_flags="")

    # Resolve the error state so the unit can recover (if needed).
    status = juju_vm.status()
    unit_name = helpers.first_unit_name(status, APP_NAME)
    unit_status = status.apps[APP_NAME].units[unit_name]
    if (
        unit_status.juju_status.current == "error"
        or unit_status.workload_status.current == "error"
    ):
        juju_vm.cli("resolved", unit_name)

    _wait_for_active(juju_vm, timeout=300)


@pytest.mark.abort_on_fail
def test_osd_devices_config_no_match(juju_vm: jubilant.Juju, deployed_microceph):
    """Test that a DSL expression matching no devices doesn't cause errors.

    When osd-devices is set but no devices match, the charm should
    remain active (this is a valid scenario).
    """
    logger.info("Testing DSL expression with no matching devices")

    # Use a DSL that won't match any devices (non-existent vendor)
    _apply_osd_config_and_wait(
        juju_vm,
        osd_devices="eq(@vendor,'nonexistent-vendor-xyz')",
        timeout=300,
    )

    # Reset config
    _clear_osd_config_and_wait(juju_vm, timeout=300)


@pytest.mark.abort_on_fail
def test_osd_devices_config_snap_has_osd_match(juju_vm: jubilant.Juju, deployed_microceph):
    """Verify that the MicroCeph snap supports the --osd-match flag.

    This confirms the snap version installed by the charm has the
    --osd-match CLI flag available, which is required for the
    osd-devices config feature.
    """
    logger.info("Verifying snap --osd-match CLI support")

    unit_name = helpers.first_unit_name(juju_vm.status(), APP_NAME)

    help_output = juju_vm.ssh(unit_name, "sudo", "microceph", "disk", "add", "--help")
    assert (
        "--osd-match" in help_output
    ), f"Snap does not support --osd-match flag. Help output:\n{help_output}"
    assert (
        "--dry-run" in help_output
    ), f"Snap does not support --dry-run flag. Help output:\n{help_output}"
    logger.info("Snap --osd-match and --dry-run flags confirmed")


@pytest.mark.abort_on_fail
def test_osd_devices_config_reapply_stays_active(juju_vm: jubilant.Juju, deployed_microceph):
    """Test that re-applying osd-devices config doesn't cause errors.

    Toggling the config off and on should not cause the charm to
    enter an error state. This verifies idempotency of the
    config-changed handler.
    """
    logger.info("Testing idempotent config re-application")

    dsl_expr = "eq(@type,'nvme')"

    # First application
    _apply_osd_config_and_wait(juju_vm, osd_devices=dsl_expr, timeout=300)

    # Clear config
    _clear_osd_config_and_wait(juju_vm, timeout=300)

    # Re-apply same config
    _apply_osd_config_and_wait(juju_vm, osd_devices=dsl_expr, timeout=300)

    # Cleanup
    _clear_osd_config_and_wait(juju_vm, timeout=300)


@pytest.mark.abort_on_fail
def test_osd_devices_config_enrolls_disk(
    juju_vm: jubilant.Juju, deployed_microceph, attached_block_device: str
):
    """Verify OSD enrollment via config, then dirty-disk rejection without wipe.

    Steps:
    1. Enroll the attached block device as an OSD via osd-devices config (with wipe).
    2. Remove the OSD, leaving leftover metadata on the disk.
    3. Re-apply osd-devices config *without* wipe — the snap should reject the
       dirty disk and the charm should block/error.
    4. Re-apply with wipe:osd — enrollment should succeed again.
    """
    unit_name = helpers.first_unit_name(juju_vm.status(), APP_NAME)
    logger.info(f"Testing OSD enrollment with block device at {attached_block_device}")

    # --- Step 1: Enroll with wipe ---
    _apply_osd_config_and_wait(
        juju_vm,
        osd_devices="eq(@type,'scsi')",
        device_add_flags="wipe:osd",
        timeout=600,
    )

    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=1)

    # --- Step 2: Remove OSD to leave the disk dirty ---
    logger.info("Removing OSD to leave dirty disk")
    _set_osd_config(juju_vm, osd_devices="", device_add_flags="")

    # Query the actual OSD number (not necessarily 0)
    osd_list_raw = juju_vm.ssh(
        unit_name,
        "sudo",
        "microceph",
        "disk",
        "list",
        "--json",
    )
    osd_list = json.loads(osd_list_raw)
    osd_num = str(osd_list["ConfiguredDisks"][0]["osd"])
    logger.info(f"Removing OSD {osd_num}")

    juju_vm.ssh(
        unit_name,
        "sudo",
        "microceph",
        "disk",
        "remove",
        osd_num,
        "--bypass-safety-checks",
        "--prohibit-crush-scaledown",
    )

    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=0)

    # --- Step 3: Re-apply config WITHOUT wipe — dirty disk should be rejected ---
    logger.info("Attempting enrollment without wipe on dirty disk")
    _set_osd_config(juju_vm, osd_devices="eq(@type,'scsi')")

    with helpers.fast_forward(juju_vm):
        helpers.wait_for_status(juju_vm, APP_NAME, ("blocked", "error"), timeout=300)

    # Reset config — the guard caught the error so the unit is blocked, not errored.
    # Clearing the config triggers a new config-changed which will unblock.
    _clear_osd_config_and_wait(juju_vm, timeout=300)

    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=0)

    # --- Step 4: Re-apply WITH wipe — should succeed ---
    logger.info("Re-enrolling dirty disk with wipe:osd flag")
    _apply_osd_config_and_wait(
        juju_vm,
        osd_devices="eq(@type,'scsi')",
        device_add_flags="wipe:osd",
        timeout=600,
    )

    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=1)

    status = juju_vm.status()
    assert (
        status.apps[APP_NAME].app_status.current == "active"
    ), f"Expected active after OSD enrollment, got {status.apps[APP_NAME].app_status.current}"

    # Cleanup config (leave the OSD enrolled)
    _clear_osd_config_and_wait(juju_vm, timeout=300)


@pytest.mark.abort_on_fail
def test_osd_devices_config_additive(
    juju_vm: jubilant.Juju, deployed_microceph, attached_block_device: str
):
    """Verify that clearing osd-devices config does not remove enrolled OSDs.

    The previous test enrolled 1 OSD and cleared the config. The OSD should
    still be present because config removal only stops future enrollment — it
    does not tear down existing OSDs.
    """
    logger.info("Verifying OSD persists after config is cleared")

    # Config was already cleared by the previous test; just verify.
    _wait_for_active(juju_vm, timeout=300)

    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=1)


@pytest.mark.abort_on_fail
def test_osd_devices_config_idempotent(
    juju_vm: jubilant.Juju, deployed_microceph, attached_block_device: str
):
    """Verify that re-applying osd-devices config doesn't duplicate OSDs.

    With 1 OSD already enrolled (from the enrolls_disk test), re-applying
    the same DSL expression should not add a second OSD for the same disk.
    """
    logger.info("Testing idempotent re-application with enrolled disk")

    _apply_osd_config_and_wait(
        juju_vm,
        osd_devices="eq(@type,'scsi')",
        device_add_flags="wipe:osd",
        timeout=600,
    )

    # Should still be exactly 1 OSD, not 2
    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=1)

    # Cleanup
    _clear_osd_config_and_wait(juju_vm, timeout=300)


@pytest.fixture(scope="module")
def second_block_device(juju_vm: jubilant.Juju, deployed_microceph) -> Iterator[str]:
    """Create and attach a second LXD block volume for multi-OSD tests.

    Uses a different size (4GB) so it can be distinguished from the first (3GB).
    """
    # Ensure config-based enrollment is disabled before attaching the disk.
    _clear_osd_config_and_wait(juju_vm, timeout=300)

    unit_name = helpers.first_unit_name(juju_vm.status(), APP_NAME)
    inst_id = lxd.get_instance_id(juju_vm, APP_NAME, unit_name)
    pool = lxd.get_lxd_storage_pool()
    model_name = juju_vm.status().model.name
    vol_name = f"osd-cfg-test2-{model_name}"

    lxd.create_and_attach_volume(pool, vol_name, inst_id, size="4GB")
    try:
        disk_path = lxd.wait_for_disk(juju_vm, unit_name, size_pattern="4G|3.7G|3.8G|3.9G")
        logger.info(f"Second block device available at {disk_path}")
        yield disk_path
    finally:
        lxd.cleanup_volume(pool, vol_name, inst_id)


@pytest.mark.abort_on_fail
def test_osd_devices_config_coexists_with_action(
    juju_vm: jubilant.Juju,
    deployed_microceph,
    attached_block_device: str,
    second_block_device: str,
):
    """Verify config-based and action-based OSD enrollment coexist.

    One OSD is already enrolled via config (attached_block_device).
    This test adds a second OSD via the add-osd action on the second
    block device, then verifies both are present.
    """
    logger.info(
        f"Testing coexistence: config OSD at {attached_block_device}, "
        f"action OSD at {second_block_device}"
    )

    # Ensure the config-based OSD is enrolled for the first disk only.
    # @devnode in snap DSL now resolves to the kernel devnode path (/dev/sdX, /dev/nvmeXnY).
    _apply_osd_config_and_wait(
        juju_vm,
        osd_devices=f"eq(@devnode,'{attached_block_device}')",
        device_add_flags="wipe:osd",
        timeout=600,
    )

    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=1)

    # Enroll second disk via action
    unit_name = helpers.first_unit_name(juju_vm.status(), APP_NAME)
    task = juju_vm.run(
        unit_name,
        "add-osd",
        {"device-id": second_block_device, "wipe": True},
        wait=600,
    )
    task.raise_on_failure()

    with helpers.fast_forward(juju_vm):
        helpers.wait_for_apps(juju_vm, APP_NAME, timeout=600)

    # Both OSDs should be present
    helpers.assert_osd_count(juju_vm, APP_NAME, expected_osds=2)
