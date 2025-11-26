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

"""Functest for MicroCeph disk ops."""

import logging
from pathlib import Path

import jubilant
import pytest

from tests import helpers
from tests.helpers import lxd

logger = logging.getLogger(__name__)

APP_NAME = "microceph"


@pytest.mark.abort_on_fail
def test_add_osd_wipe(juju: jubilant.Juju, microceph_charm: Path):
    """Deploy MicroCeph and verify add-osd action with wipe=True on a dirty disk."""
    logger.info("Deploying MicroCeph")
    juju.deploy(
        str(microceph_charm),
        APP_NAME,
        config={"snap-channel": "squid/edge"},
        constraints={
            "virt-type": "virtual-machine",
            "root-disk": "16G",
            "mem": "4G",
        },
    )

    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=3600)

    unit_name = helpers.first_unit_name(juju.status(), APP_NAME)

    # Prepare lxd vol to add as bdev
    inst_id = lxd.get_instance_id(juju, APP_NAME, unit_name)
    logger.info(f"Instance ID: {inst_id}")

    chosen_pool = lxd.get_lxd_storage_pool()
    logger.info(f"Using LXD storage pool: {chosen_pool}")

    model_name = juju.status().model.name
    vol_name = f"wipe-test-{model_name}"

    try:
        lxd.create_and_attach_volume(chosen_pool, vol_name, inst_id)

        disk_path = lxd.wait_for_disk(juju, unit_name)
        logger.info(f"Found disk at {disk_path}")

        logger.info("Formatting disk to make it dirty")
        juju.ssh(unit_name, f"sudo mkfs.ext4 -F {disk_path}")

        logger.info("Running add-osd action with wipe=true")
        action = juju.run(unit_name, "add-osd", {"device-id": disk_path, "wipe": True}, wait=1200)
        action.raise_on_failure()

        logger.info("Verifying OSD count")
        helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)

    finally:
        lxd.cleanup_volume(chosen_pool, vol_name, inst_id)
