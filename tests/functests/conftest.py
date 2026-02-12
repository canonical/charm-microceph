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

"""Shared fixtures for MicroCeph functional tests."""

import logging
from pathlib import Path
from typing import Iterator

import jubilant
import pytest

from tests import helpers
from tests.helpers import lxd

logger = logging.getLogger(__name__)

APP_NAME = "microceph"
SNAP_CHANNEL = "squid/edge"
VM_CONSTRAINTS = {"virt-type": "virtual-machine", "root-disk": "16G", "mem": "4G"}


@pytest.fixture(scope="module")
def deployed_microceph(juju: jubilant.Juju, microceph_charm: Path) -> str:
    """Deploy MicroCeph in a VM and wait for active/idle.

    Returns the unit name.
    """
    logger.info("Deploying MicroCeph")
    juju.deploy(
        str(microceph_charm),
        APP_NAME,
        config={"snap-channel": SNAP_CHANNEL},
        constraints=VM_CONSTRAINTS,
    )

    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=3600)

    return helpers.first_unit_name(juju.status(), APP_NAME)


@pytest.fixture(scope="module")
def attached_lxd_volume(juju: jubilant.Juju, deployed_microceph: str) -> Iterator[str]:
    """Create and attach a 1 GB LXD block volume to the deployed VM.

    Yields the disk path inside the VM (e.g. ``/dev/sdb``).
    Cleans up the volume after the module finishes.
    """
    unit_name = deployed_microceph

    inst_id = lxd.get_instance_id(juju, APP_NAME, unit_name)
    logger.info(f"Instance ID: {inst_id}")

    pool = lxd.get_lxd_storage_pool()
    logger.info(f"Using LXD storage pool: {pool}")

    model_name = juju.status().model.name
    vol_name = f"functest-{model_name}"

    lxd.create_and_attach_volume(pool, vol_name, inst_id)
    try:
        disk_path = lxd.wait_for_disk(juju, unit_name)
        logger.info(f"Block device available at {disk_path}")
        yield disk_path
    finally:
        lxd.cleanup_volume(pool, vol_name, inst_id)
