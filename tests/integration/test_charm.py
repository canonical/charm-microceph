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

"""Tests for the MicroCeph charm."""

import json
import logging
from pathlib import Path

import jubilant
import pytest
import yaml

from tests.integration import helpers

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
CEPHCLIENT_APP = "cephclient"
LOOP_OSD_SPEC = "1G,3"


@pytest.fixture(scope="module")
def deployed_apps(juju: jubilant.Juju, microceph_charm: Path, cephclient_charm: Path):
    """Deploy MicroCeph and the cephclient tester charm."""
    logger.info("Deploying charms: %s and %s", APP_NAME, CEPHCLIENT_APP)
    juju.deploy(str(microceph_charm), APP_NAME)
    juju.deploy(str(cephclient_charm), CEPHCLIENT_APP)
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=1000)
    helpers.ensure_loop_osd(juju, APP_NAME, LOOP_OSD_SPEC)
    return (APP_NAME, CEPHCLIENT_APP)


@pytest.fixture(scope="module")
def integrated_apps(juju: jubilant.Juju, deployed_apps):
    """Integrate the ceph relation between the deployed applications."""
    juju.integrate(f"{CEPHCLIENT_APP}:ceph", f"{APP_NAME}:ceph")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=180)
    return deployed_apps


@pytest.fixture(scope="module")
def integrated_apps_rgw_ready(juju: jubilant.Juju, deployed_apps):
    """Integrate the ceph-rgw-ready relation between the deployed applications."""
    juju.integrate(
        f"{CEPHCLIENT_APP}:ceph-rgw-ready",
        f"{APP_NAME}:ceph-rgw-ready",
    )
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=180)
    return deployed_apps


@pytest.fixture(scope="module")
def relation_removed(juju: jubilant.Juju, integrated_apps):
    """Remove the ceph relation between the applications."""
    juju.remove_relation(f"{CEPHCLIENT_APP}:ceph", f"{APP_NAME}:ceph")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=180)
    return integrated_apps


@pytest.fixture(scope="module")
def relation_removed_rgw_ready(juju: jubilant.Juju, integrated_apps):
    """Remove the ceph-rgw-ready relation between the applications."""
    juju.remove_relation(
        f"{CEPHCLIENT_APP}:ceph-rgw-ready",
        f"{APP_NAME}:ceph-rgw-ready",
    )
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=180)
    return integrated_apps


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_build_and_deploy(juju: jubilant.Juju, deployed_apps):
    """Build the charms, deploy them, and ensure the applications settle."""
    status = juju.status()
    assert jubilant.all_active(status, *deployed_apps)


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_integrate(juju: jubilant.Juju, integrated_apps):
    """Integrate the charms over the ceph relation."""
    status = juju.status()
    assert jubilant.all_active(status, *integrated_apps)


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_integrate_ceph_rgw_ready(juju: jubilant.Juju, integrated_apps_rgw_ready):
    """Integrate the charms over the ceph-rgw-ready relation."""
    status = juju.status()
    assert jubilant.all_active(status, *integrated_apps_rgw_ready)


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_broker_request_processed(juju: jubilant.Juju, integrated_apps):
    """Check if relation data is updated after the broker request completes."""
    data, broker_rsp_key = helpers.wait_for_broker_response(juju, CEPHCLIENT_APP, APP_NAME)
    assert broker_rsp_key in data
    broker_rsp_value = json.loads(data.get(broker_rsp_key))
    assert broker_rsp_value.get("exit-code") == 0


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_remove_integration(juju: jubilant.Juju, relation_removed):
    """Remove ceph integration and ensure both applications stay healthy."""
    status = juju.status()
    assert jubilant.all_active(status, *relation_removed)


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_remove_integration_rgw_ready(juju: jubilant.Juju, relation_removed_rgw_ready):
    """Remove ceph-rgw-ready integration and ensure both applications stay healthy."""
    status = juju.status()
    assert jubilant.all_active(status, *relation_removed_rgw_ready)
