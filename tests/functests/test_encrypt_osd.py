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

"""Functest for MicroCeph disk ops."""

import logging

import jubilant
import pytest

from tests import helpers
from tests.functests.conftest import APP_NAME

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_add_osd_encrypt(juju: jubilant.Juju, attached_lxd_volume: str):
    """Verify add-osd action with encrypt=True on a disk."""
    disk_path = attached_lxd_volume
    unit_name = helpers.first_unit_name(juju.status(), APP_NAME)

    logger.info("Running add-osd action with encrypt=true")
    action = juju.run(unit_name, "add-osd", {"device-id": disk_path, "encrypt": True}, wait=1200)
    action.raise_on_failure()

    logger.info("Verifying OSD count")
    helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)

    logger.info("Checking LUKS header on disk")
    luks_dump = juju.ssh(unit_name, f"sudo cryptsetup luksDump {disk_path}")
    assert "LUKS header information" in luks_dump


@pytest.mark.abort_on_fail
def test_add_osd_encrypt_no_dmcrypt(juju: jubilant.Juju, attached_lxd_volume: str):
    """Verify add-osd encrypt fails gracefully when dm-crypt is unavailable."""
    disk_path = attached_lxd_volume
    unit_name = helpers.first_unit_name(juju.status(), APP_NAME)

    # Move the dm_crypt module file so modprobe can't load it
    juju.ssh(
        unit_name,
        "sudo bash -c '"
        "mv /lib/modules/$(uname -r)/kernel/drivers/md/dm-crypt.ko.zst"
        " /lib/modules/$(uname -r)/kernel/drivers/md/dm-crypt.ko.zst.bak"
        " && depmod -a'",
    )

    try:
        # Unload the module if currently loaded
        try:
            juju.ssh(unit_name, "sudo", "modprobe", "-r", "dm_crypt")
        except jubilant.CLIError:
            pass

        with pytest.raises(jubilant.TaskError, match="dm_crypt") as exc_info:
            juju.run(unit_name, "add-osd", {"device-id": disk_path, "encrypt": True}, wait=120)
        assert exc_info.value.task.status == "failed"

    finally:
        # Restore the module file
        juju.ssh(
            unit_name,
            "sudo bash -c '"
            "mv /lib/modules/$(uname -r)/kernel/drivers/md/dm-crypt.ko.zst.bak"
            " /lib/modules/$(uname -r)/kernel/drivers/md/dm-crypt.ko.zst"
            " && depmod -a'",
        )
