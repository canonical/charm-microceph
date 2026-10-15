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

"""Functional coverage for role-managed RGW placement."""

import json
import os
import shutil
import tempfile
import time

import jubilant
import pytest

from tests import helpers
from tests.functests.conftest import APP_NAME
from tests.functests.test_storage_role import create_dummy_provider_charm, zip_dir_to_charm


@pytest.fixture(scope="module")
def snap_channel() -> str:
    """Deploy a snap channel that can accept role-managed RGW placement."""
    # REVERT BEFORE MERGE: use tentacle/edge once RGW placement is available there.
    return "tentacle/edge/rgw-placement"


@pytest.fixture(scope="module")
def placement_microceph(juju: jubilant.Juju, deployed_microceph: str) -> str:
    """Require RGW placement capabilities from the deployed Store snap."""
    response = json.loads(
        juju.ssh(
            deployed_microceph,
            "sudo",
            "curl",
            "--silent",
            "--show-error",
            "--unix-socket",
            "/var/snap/microceph/common/state/control.socket",
            "http://localhost/1.0/cluster/capabilities",
        )
    )
    supported = set((response.get("metadata") or {}).get("supported") or [])
    missing = {"declarative-placement", "placement-rgw"} - supported
    if missing:
        pytest.fail(f"RGW role test requires MicroCeph snap capabilities: {sorted(missing)}")
    return deployed_microceph


def get_placement(juju: jubilant.Juju, unit_name: str) -> dict:
    """Read the snap's placement policy and observed services."""
    return json.loads(
        juju.ssh(
            unit_name,
            "sudo",
            "curl",
            "--silent",
            "--show-error",
            "--unix-socket",
            "/var/snap/microceph/common/state/control.socket",
            "http://localhost/1.0/placement",
        )
    )["metadata"]


def wait_for_rgw(
    juju: jubilant.Juju,
    unit_name: str,
    hostname: str,
    *,
    enabled: bool,
    refusal_message: str,
    timeout_message: str,
) -> dict:
    """Wait for the observed gateway state, including its plaintext port when enabled."""
    timeout, interval = (360, 5) if enabled else (120, 2)
    with helpers.fast_forward(juju):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            placement = get_placement(juju, unit_name)
            observed = next(
                (member for member in placement["observed"] if member["member"] == hostname),
                {},
            )
            if enabled:
                ready = observed.get("rgw") and observed.get("rgw_frontend", {}).get("port") == 80
            else:
                ready = observed.get("rgw") is False
            if ready:
                return observed
            if placement.get("placement_refusal"):
                pytest.fail(f"{refusal_message}: {placement['placement_refusal']}")
            time.sleep(interval)
        pytest.fail(timeout_message)


@pytest.mark.abort_on_fail
def test_rgw_role_lifecycle(
    juju: jubilant.Juju, placement_microceph: str, attached_lxd_volume: str
):
    """Stop RGW by explicit assignment, not by removing its provider."""
    unit_name = placement_microceph
    provider = "rgw-role-provider"
    juju.config(APP_NAME, {"role-managed": True, "default-pool-size": 1})
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=300)

    tmp_dir = tempfile.mkdtemp(prefix="rgw-role-provider-")
    charm_path = os.path.join(tempfile.gettempdir(), "rgw-role-provider.charm")
    try:
        create_dummy_provider_charm(tmp_dir, roles=["control", "storage"])
        zip_dir_to_charm(tmp_dir, charm_path)
        juju.deploy(charm_path, provider)
        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, provider, timeout=600)
        juju.integrate(APP_NAME, provider)
        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, APP_NAME, provider, timeout=600)

        hostname = juju.ssh(unit_name, "hostname").strip()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            placement = get_placement(juju, unit_name)
            member = (placement.get("policy") or {}).get("members", {}).get(hostname, {})
            if member.get("storage_eligible") is True:
                break
            if placement.get("placement_refusal"):
                pytest.fail(f"Storage placement refused: {placement['placement_refusal']}")
            time.sleep(2)
        else:
            pytest.fail("Storage assignment was not applied before OSD enrollment")

        action = juju.run(unit_name, "add-osd", {"device-id": attached_lxd_volume}, wait=1200)
        action.raise_on_failure()
        helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)

        relation_id = juju.exec(
            "relation-ids", "role-assignment", unit=f"{provider}/0"
        ).stdout.strip()

        def publish_roles(roles, workload_params=None):
            assignment = {
                "microceph/0": {
                    "status": "assigned",
                    "roles": roles,
                    "workload-params": workload_params or {},
                }
            }
            juju.exec(
                "relation-set",
                "-r",
                relation_id,
                "--app",
                f"assignments={json.dumps(assignment)}",
                unit=f"{provider}/0",
            )

        publish_roles(["control", "storage", "gateway"], {"flavors": ["rgw"]})
        wait_for_rgw(
            juju,
            unit_name,
            hostname,
            enabled=True,
            refusal_message="RGW placement refused",
            timeout_message="RGW did not start on the assigned member",
        )

        publish_roles(["control", "storage"])
        observed = wait_for_rgw(
            juju,
            unit_name,
            hostname,
            enabled=False,
            refusal_message="RGW revocation refused",
            timeout_message="Explicitly removing the RGW role did not stop RGW",
        )
        assert observed["control"] is True
        helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)

        publish_roles(["control", "storage", "gateway"], {"flavors": ["rgw"]})
        wait_for_rgw(
            juju,
            unit_name,
            hostname,
            enabled=True,
            refusal_message="RGW restart refused",
            timeout_message="RGW did not restart on the assigned member",
        )

        juju.remove_relation(APP_NAME, provider)
        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, APP_NAME, provider, timeout=600)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                placement = get_placement(juju, unit_name)
                observed = next(
                    (member for member in placement["observed"] if member["member"] == hostname),
                    {},
                )
                if (placement.get("policy") or {}).get("members") == {} and observed.get("rgw"):
                    break
                if placement.get("placement_refusal"):
                    pytest.fail(f"Empty placement refused: {placement['placement_refusal']}")
                time.sleep(2)
            else:
                pytest.fail("Provider removal did not preserve RGW with empty placement")
        assert observed["control"] is True
        helpers.assert_osd_count(juju, APP_NAME, expected_osds=1)
        assert juju.ssh(
            unit_name,
            "curl",
            "--silent",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            "http://127.0.0.1/",
        ).strip() in {"200", "403"}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.exists(charm_path):
            os.remove(charm_path)
