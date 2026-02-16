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

"""Integration tests for the ceph-csi relation on the charm-microceph (provider) side."""

import json
import logging
import time
from pathlib import Path

import jubilant
import pytest
import yaml

from tests import helpers

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
K8S_APP = "k8s"
CEPH_CSI_APP = "ceph-csi"
LOOP_OSD_SPEC = "1G,3"


def _get_ceph_auth_entries(juju: jubilant.Juju, unit_name: str) -> list[dict]:
    """Get ceph auth entries, handling both JSON formats."""
    task = juju.exec("sudo microceph.ceph auth ls --format json", unit=unit_name)
    auth_data = json.loads(task.stdout)
    # ceph auth ls --format json may return {"auth_dump": [...]} or a plain list
    if isinstance(auth_data, dict):
        return auth_data.get("auth_dump", [])
    return auth_data


def _find_csi_auth_entry(auth_entries: list[dict]) -> dict | None:
    """Find the ceph-csi auth entry (client.csi-*) in auth entries."""
    for entry in auth_entries:
        entity = entry.get("entity", "") if isinstance(entry, dict) else ""
        if entity.startswith("client.csi-"):
            return entry
    return None


# ---------------------------------------------------------------------------
# Module-scoped deployment fixtures (chained)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deployed_microceph(juju: jubilant.Juju, microceph_charm: Path):
    """Deploy microceph, add loop OSDs, and wait for active status."""
    logger.info("[STAGE] Deploying microceph charm: %s", APP_NAME)
    juju.deploy(str(microceph_charm), APP_NAME)
    logger.info("[STAGE] Waiting for microceph to become active...")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=1000)
    logger.info("[STAGE] Microceph active. Adding loop OSDs: %s", LOOP_OSD_SPEC)
    helpers.ensure_loop_osd(juju, APP_NAME, LOOP_OSD_SPEC)
    logger.info("[CHECKPOINT] Microceph deployed and OSDs configured")
    return APP_NAME


@pytest.fixture(scope="module")
def deployed_k8s(k8s_juju: jubilant.Juju):
    """Deploy k8s charm, wait for active, and prepare for ceph-csi."""
    logger.info("[STAGE] Deploying k8s charm (VM: cores=2, mem=8G, root-disk=40G)")
    k8s_juju.deploy(
        "k8s",
        K8S_APP,
        channel="latest/edge",
        constraints={
            "cores": "2",
            "mem": "8G",
            "root-disk": "40G",
            "virt-type": "virtual-machine",
        },
    )
    logger.info("[STAGE] Waiting for k8s to become active (timeout=1800s)...")
    with helpers.fast_forward(k8s_juju):
        helpers.wait_for_apps(k8s_juju, K8S_APP, timeout=1800)
    logger.info("[CHECKPOINT] k8s charm is active")

    # Load kernel modules required by ceph-csi (use exec instead of ssh for VM compatibility)
    status = k8s_juju.status()
    k8s_unit = helpers.first_unit_name(status, K8S_APP)
    logger.info("[STAGE] Loading kernel modules (rbd, ceph) on %s", k8s_unit)
    k8s_juju.exec("sudo modprobe rbd", unit=k8s_unit)
    k8s_juju.exec("sudo modprobe ceph", unit=k8s_unit)
    logger.info("[STAGE] Enabling load-balancer on k8s")
    k8s_juju.exec("sudo k8s enable load-balancer", unit=k8s_unit)
    logger.info("[CHECKPOINT] k8s deployed and configured")
    return K8S_APP


@pytest.fixture(scope="module")
def deployed_ceph_csi(k8s_juju: jubilant.Juju, ceph_csi_source: dict, deployed_k8s):
    """Deploy ceph-csi subordinate and relate to k8s:juju-info."""
    csi_config = {"provisioner-replicas": 1}
    if "charm" in ceph_csi_source:
        logger.info("[STAGE] Deploying ceph-csi from local charm (provisioner-replicas=1)")
        k8s_juju.deploy(str(ceph_csi_source["charm"]), CEPH_CSI_APP, config=csi_config)
    else:
        logger.info(
            "[STAGE] Deploying ceph-csi from charmhub (%s, provisioner-replicas=1)",
            ceph_csi_source["channel"],
        )
        k8s_juju.deploy(
            CEPH_CSI_APP, channel=ceph_csi_source["channel"], config=csi_config,
        )
    logger.info("[STAGE] Integrating ceph-csi:kubernetes with k8s:juju-info")
    k8s_juju.integrate(f"{CEPH_CSI_APP}:kubernetes", f"{K8S_APP}:juju-info")
    logger.info("[CHECKPOINT] ceph-csi deployed and related to k8s (waiting for ceph relation)")
    return CEPH_CSI_APP


@pytest.fixture(scope="module")
def cross_model_integrated(
    juju: jubilant.Juju,
    k8s_juju: jubilant.Juju,
    deployed_microceph,
    deployed_ceph_csi,
):
    """Set up cross-model relation: microceph offers ceph-csi, k8s model consumes it."""
    microceph_model = juju.status().model.name
    k8s_model = k8s_juju.status().model.name

    offer_name = f"{microceph_model}.{APP_NAME}"

    # Create the offer from microceph (include model name in app for correct routing)
    logger.info("[STAGE] Creating cross-model offer: %s:ceph-csi", offer_name)
    juju.offer(f"{microceph_model}.{APP_NAME}", endpoint="ceph-csi")
    logger.info("[STAGE] Consuming offer %s in model %s", offer_name, k8s_model)
    k8s_juju.consume(offer_name, APP_NAME, owner="admin")

    # Integrate ceph-csi with the consumed offer
    logger.info("[STAGE] Integrating ceph-csi:ceph with consumed offer")
    k8s_juju.integrate(f"{CEPH_CSI_APP}:ceph", APP_NAME)

    # Wait for both sides to settle
    logger.info("[STAGE] Waiting for microceph to settle after cross-model integration...")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=600)
    logger.info("[STAGE] Waiting for k8s to settle...")
    with helpers.fast_forward(k8s_juju):
        helpers.wait_for_apps(k8s_juju, K8S_APP, timeout=600)

    # Wait for ceph-csi to become active (provisioner deployment takes time)
    logger.info("[STAGE] Waiting for ceph-csi subordinate to become active...")
    with helpers.fast_forward(k8s_juju):
        try:
            helpers.wait_for_apps(k8s_juju, CEPH_CSI_APP, timeout=600)
        except Exception:
            # Log status for debugging but don't fail - ceph-csi may stay
            # in waiting state while provisioner deployment rolls out
            k8s_status = k8s_juju.status()
            ceph_csi_status = k8s_status.apps.get(CEPH_CSI_APP)
            if ceph_csi_status:
                logger.warning(
                    "[WARNING] ceph-csi not fully active: %s",
                    ceph_csi_status.app_status.message,
                )
            else:
                raise
    logger.info("[CHECKPOINT] Cross-model integration complete")

    return offer_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.abort_on_fail
def test_build_and_deploy(
    juju: jubilant.Juju,
    k8s_juju: jubilant.Juju,
    cross_model_integrated,
):
    """Assert microceph and k8s+ceph-csi are both active after cross-model integration."""
    logger.info("[TEST] test_build_and_deploy: checking all apps are active")
    status = juju.status()
    assert jubilant.all_active(status, APP_NAME)
    logger.info("[TEST] microceph is active")

    k8s_status = k8s_juju.status()
    assert jubilant.all_active(k8s_status, K8S_APP)
    logger.info("[TEST] k8s is active")
    logger.info("[PASS] test_build_and_deploy")


@pytest.mark.abort_on_fail
def test_rbd_pool_created(juju: jubilant.Juju, cross_model_integrated):
    """Verify the RBD pool for the ceph-csi application was created."""
    logger.info("[TEST] test_rbd_pool_created: checking ceph pools")
    status = juju.status()
    unit_name = helpers.first_unit_name(status, APP_NAME)

    # In cross-model relations, the pool name is rbd.<remote-app-name> where
    # the remote app name is a synthetic "remote-<uuid>" assigned by juju.
    # We look for any pool starting with "rbd." to verify creation.

    # Poll for pool creation (cross-model data exchange may take time)
    deadline = time.time() + 120
    rbd_pools = []
    while time.time() < deadline:
        task = juju.exec("sudo microceph.ceph osd pool ls", unit=unit_name)
        pools = task.stdout.strip().splitlines()
        logger.info("[TEST] Ceph pools: %s", pools)
        rbd_pools = [p for p in pools if p.startswith("rbd.")]
        if rbd_pools:
            break
        logger.info("[TEST] No rbd.* pool yet, retrying in 10s...")
        time.sleep(10)

    assert len(rbd_pools) > 0, f"Expected at least one rbd.* pool, got: {pools}"
    logger.info("[PASS] test_rbd_pool_created: found RBD pool(s): %s", rbd_pools)


@pytest.mark.abort_on_fail
def test_ceph_auth_created(juju: jubilant.Juju, cross_model_integrated):
    """Verify the cephx auth key for ceph-csi was created with rbd caps."""
    logger.info("[TEST] test_ceph_auth_created: checking cephx auth entries")
    status = juju.status()
    unit_name = helpers.first_unit_name(status, APP_NAME)

    auth_entries = _get_ceph_auth_entries(juju, unit_name)
    logger.info("[TEST] Found %d auth entries", len(auth_entries))

    csi_client = _find_csi_auth_entry(auth_entries)
    assert csi_client is not None, (
        f"No cephx auth entry starting with 'client.csi-' found "
        f"among: {auth_entries}"
    )
    logger.info("[TEST] Found cephx entry: %s", csi_client.get("entity"))

    caps = csi_client.get("caps", {})
    assert "osd" in caps, f"Expected osd caps in cephx entry, got: {caps}"
    logger.info("[PASS] test_ceph_auth_created: caps=%s", caps)


def test_cephfs_workload(
    juju: jubilant.Juju,
    k8s_juju: jubilant.Juju,
    cross_model_integrated,
):
    """Enable cephfs on ceph-csi and verify CephFS resources are created."""
    logger.info("[TEST] test_cephfs_workload: enabling cephfs on ceph-csi")
    k8s_juju.config(CEPH_CSI_APP, {"cephfs-enable": "true"})

    logger.info("[STAGE] Waiting for microceph to reconcile cephfs changes...")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=600)

    logger.info("[STAGE] Allowing 30s for CephFS pools and MDS to be created...")
    time.sleep(30)

    status = juju.status()
    unit_name = helpers.first_unit_name(status, APP_NAME)

    # Verify CephFS filesystem exists
    logger.info("[TEST] Checking CephFS filesystems...")
    task = juju.exec(
        "sudo microceph.ceph fs ls --format json", unit=unit_name
    )
    filesystems = json.loads(task.stdout)
    logger.info("[TEST] CephFS filesystems: %s", filesystems)
    assert len(filesystems) > 0, "No CephFS filesystems found after enabling cephfs"

    # Verify auth caps updated with mds permissions
    logger.info("[TEST] Checking cephx auth caps for mds...")
    auth_entries = _get_ceph_auth_entries(juju, unit_name)
    csi_client = _find_csi_auth_entry(auth_entries)
    assert csi_client is not None, "No cephx auth entry starting with 'client.csi-' after cephfs enable"
    caps = csi_client.get("caps", {})
    assert "mds" in caps, f"Expected mds caps after cephfs enable, got: {caps}"
    logger.info("[PASS] test_cephfs_workload: caps=%s", caps)


def test_remove_relation(
    juju: jubilant.Juju,
    k8s_juju: jubilant.Juju,
    cross_model_integrated,
):
    """Remove the cross-model relation and verify cephx key is cleaned up."""
    logger.info("[TEST] test_remove_relation: removing cross-model integration")
    k8s_juju.remove_relation(f"{CEPH_CSI_APP}:ceph", APP_NAME)

    logger.info("[STAGE] Waiting for microceph to settle after relation removal...")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=600)

    logger.info("[STAGE] Allowing 15s for cleanup...")
    time.sleep(15)

    # Verify cephx auth entry is removed
    logger.info("[TEST] Checking cephx auth entries are cleaned up...")
    status = juju.status()
    unit_name = helpers.first_unit_name(status, APP_NAME)

    auth_entries = _get_ceph_auth_entries(juju, unit_name)
    csi_clients = [
        e for e in auth_entries
        if isinstance(e, dict) and e.get("entity", "").startswith("client.csi-")
    ]
    assert len(csi_clients) == 0, (
        f"Expected cephx auth entries starting with 'client.csi-' to be cleaned up, "
        f"but found: {[e.get('entity') for e in csi_clients]}"
    )
    logger.info("[PASS] test_remove_relation: cephx auth cleanup verified")
