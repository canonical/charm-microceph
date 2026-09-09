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

"""Functest for MicroCeph storage role gating and reconciliation."""

import json
import logging
import os
import shutil
import tempfile

import jubilant
import pytest

from tests import helpers
from tests.functests.conftest import APP_NAME

logger = logging.getLogger(__name__)

METADATA_CONTENTS = """
name: dummy-provider
summary: Dummy role-assignment Provider charm for functional tests.
description: Dummy role-assignment Provider charm.
provides:
  role-assignment:
    interface: role-assignment
"""

MANIFEST_CONTENTS = """
charmcraft-version: 4.3.1
charmcraft-started-at: '2026-08-24T19:13:50.480933'
bases:
- name: ubuntu
  channel: '24.04'
  architectures:
  - amd64
analysis:
  attributes:
  - name: language
    result: unknown
  - name: framework
    result: unknown
"""

LIFECYCLE_HOOK = """#!/bin/sh
status-set active Active
exit 0
"""


def create_dummy_provider_charm(target_dir):
    """Create a minimal bash-hook role-assignment Provider charm on the fly."""
    os.makedirs(target_dir, exist_ok=True)
    os.makedirs(os.path.join(target_dir, "hooks"), exist_ok=True)

    with open(os.path.join(target_dir, "metadata.yaml"), "w") as f:
        f.write(METADATA_CONTENTS)

    with open(os.path.join(target_dir, "manifest.yaml"), "w") as f:
        f.write(MANIFEST_CONTENTS)

    # All lifecycle hooks can just exit 0
    for hook in ["install", "start", "config-changed"]:
        hook_path = os.path.join(target_dir, "hooks", hook)
        with open(hook_path, "w") as f:
            f.write(LIFECYCLE_HOOK)
        os.chmod(hook_path, 0o755)

    # Relation hooks publish assignments giving microceph/0 the storage role
    assignments_json = json.dumps(
        {
            "microceph/0": {
                "status": "assigned",
                "roles": ["storage"],
                "workload-params": {},
            }
        }
    )

    relation_hook = f"""#!/bin/sh
if is-leader; then
  relation-set -r "$JUJU_RELATION_ID" --app assignments='{assignments_json}'
fi
exit 0
"""
    for hook in [
        "role-assignment-relation-joined",
        "role-assignment-relation-changed",
        "leader-settings-changed",
    ]:
        hook_path = os.path.join(target_dir, "hooks", hook)
        with open(hook_path, "w") as f:
            f.write(relation_hook)
        os.chmod(hook_path, 0o755)


def zip_dir_to_charm(src_dir, dest_zip_path):
    """Zip a directory's contents into a Juju-deployable .charm package."""
    import zipfile

    with zipfile.ZipFile(dest_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, src_dir)
                zipf.write(abs_path, rel_path)


@pytest.mark.abort_on_fail
def test_storage_role_gating_and_reconciliation(
    juju: jubilant.Juju, deployed_microceph: str, attached_lxd_volume: str
):
    """Verify that OSD enrollment is blocked when ineligible, and allowed once role-assigned."""
    unit_name = deployed_microceph
    disk_path = attached_lxd_volume

    # Enable role-managed mode (blocks any OSD enrollment since no relation exists yet)
    logger.info("Enabling role-managed placement mode")
    juju.config(APP_NAME, {"role-managed": True})

    # Wait for the change to settle
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=300)

    # Verify that add-osd action fails gracefully when ineligible
    logger.info("Verifying add-osd fails while unit is not assigned the storage role")
    with pytest.raises(jubilant.TaskError) as exc_info:
        juju.run(unit_name, "add-osd", {"device-id": disk_path}, wait=120)
    assert "storage" in exc_info.value.task.message or "failed" in exc_info.value.task.status

    # Create and deploy our dummy role-assignment provider charm on the fly
    tmp_dir = tempfile.mkdtemp(prefix="dummy-provider-")
    charm_path = os.path.join(tempfile.gettempdir(), "dummy-provider.charm")
    try:
        logger.info(f"Creating dummy provider charm in temporary directory: {tmp_dir}")
        create_dummy_provider_charm(tmp_dir)

        logger.info(f"Zipping dummy provider charm directory into package: {charm_path}")
        zip_dir_to_charm(tmp_dir, charm_path)

        logger.info("Deploying dummy role-assignment provider charm")
        juju.deploy(charm_path, "dummy-provider")
        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, "dummy-provider", timeout=600)

        # Relate microceph with dummy-provider
        logger.info("Relating microceph with dummy-provider")
        juju.integrate(f"{APP_NAME}", "dummy-provider")

        # Wait for both dummy-provider and microceph to stabilize
        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, APP_NAME, "dummy-provider", timeout=600)

        # Configure both role-managed and osd-devices configuration.
        logger.info("Enabling role-managed placement mode and configuring osd-devices")
        juju.config(APP_NAME, {"role-managed": True, "osd-devices": "eq(@type,'virtio')"})

        # Run add-osd action which should now succeed
        logger.info("Verifying add-osd action succeeds")
        action = juju.run(unit_name, "add-osd", {"device-id": disk_path}, wait=1200)
        action.raise_on_failure()

        # Verify OSD was successfully enrolled in the cluster
        helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)

    finally:
        # Cleanup temporary directory and charm package
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.exists(charm_path):
            os.remove(charm_path)
