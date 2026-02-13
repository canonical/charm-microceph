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

import jubilant
import pytest

from tests import helpers
from tests.functests.conftest import APP_NAME

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_add_osd_wipe(juju: jubilant.Juju, attached_lxd_volume: str):
    """Verify add-osd action with wipe=True on a dirty disk."""
    disk_path = attached_lxd_volume
    unit_name = helpers.first_unit_name(juju.status(), APP_NAME)

    logger.info("Formatting disk to make it dirty")
    juju.ssh(unit_name, f"sudo mkfs.ext4 -F {disk_path}")

    logger.info("Running add-osd action with wipe=true")
    action = juju.run(unit_name, "add-osd", {"device-id": disk_path, "wipe": True}, wait=1200)
    action.raise_on_failure()

    logger.info("Verifying OSD count")
    helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)
