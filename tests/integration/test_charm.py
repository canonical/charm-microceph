# Copyright 2023 Canonical Ltd.
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

"""Tests for Microceph charm."""

import asyncio
import json
import logging
from pathlib import Path

import pytest
import test_utils
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm-under-test and deploy it together with test charms."""
    # Build and deploy charm from local source folder
    charm = await ops_test.build_charm(".", verbosity="debug")
    cephclient_charm_path = (Path(__file__).parent / "testers" / "cephclient").absolute()
    test_charm = await ops_test.build_charm(cephclient_charm_path, verbosity="debug")

    # Deploy the charm and wait for active/idle status
    await asyncio.gather(
        ops_test.model.deploy(charm, application_name=APP_NAME),
        ops_test.model.deploy(test_charm, application_name="cephclient"),
    )
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, "cephclient"], status="active", raise_on_blocked=True, timeout=1000
        )


@pytest.mark.abort_on_fail
async def test_integrate(ops_test: OpsTest):
    """Integrate the charms over ceph relation."""
    await ops_test.model.integrate("cephclient:ceph", f"{APP_NAME}:ceph")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, "cephclient"], status="active", raise_on_blocked=True, timeout=180
        )


@pytest.mark.abort_on_fail
async def test_broker_request_processed(ops_test: OpsTest):
    """Check if relation data is updated.

    Check if broker request has been handled and relation data bag
    is updated with response.
    """
    cephclient_unit = ops_test.model.applications["cephclient"].units[0]
    microceph_unit = ops_test.model.applications["microceph"].units[0]

    data = test_utils.get_relation_data(
        cephclient_unit.name, "ceph", microceph_unit.name, ops_test.model.name
    )
    broker_rsp_key = f"broker-rsp-{cephclient_unit.name.replace('/', '-')}"
    assert broker_rsp_key in data
    broker_rsp_value = json.loads(data.get(broker_rsp_key))
    assert broker_rsp_value.get("exit-code") == 0


@pytest.mark.abort_on_fail
async def test_remove_integration(ops_test: OpsTest):
    """Remove ceph integration."""
    await ops_test.juju("remove-relation", "cephclient:ceph", f"{APP_NAME}:ceph")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, "cephclient"], status="active", raise_on_blocked=True, timeout=180
        )
