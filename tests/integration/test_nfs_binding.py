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

"""Integration tests for the MicroCeph charm's NFS network binding.

These exercise the *real* Juju network bindings rather than mocked addresses.
On a single-network model the nfs binding resolves to the same address as
the public binding.
"""

import ipaddress
import logging
import time
from pathlib import Path
from typing import Callable

import jubilant
import pytest
import yaml

from tests import helpers

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def _peers_self_data(juju: jubilant.Juju, unit: str) -> dict[str, str]:
    """Return the data a unit has published about itself on the peers relation.

    Uses ``relation-get`` via ``juju exec`` (which runs with a hook-tool
    context) so the result is the canonical databag, independent of how
    ``juju show-unit`` happens to format its output.
    """
    # relation-get all keys ("-") of {unit}'s own databag on the peers relation;
    # the relation id comes from `relation-ids peers`.
    script = f'relation-get --format=yaml -r "$(relation-ids peers | head -n1)" - {unit}'
    task = juju.exec(script, unit=unit)
    task.raise_on_failure()
    return yaml.safe_load(task.stdout) or {}


def _wait_for_peers_data(
    juju: jubilant.Juju,
    unit: str,
    predicate: Callable[[dict[str, str]], bool],
    *,
    description: str,
    timeout: int = 600,
    interval: int = 10,
) -> dict[str, str]:
    """Poll the unit's peers self-data until *predicate* holds, then return it."""
    deadline = time.time() + timeout
    last: dict[str, str] = {}
    with helpers.fast_forward(juju):
        while time.time() < deadline:
            last = _peers_self_data(juju, unit)
            if predicate(last):
                return last
            time.sleep(interval)
    raise AssertionError(f"Timed out waiting for peers data to {description}; last: {last}")


@pytest.fixture(scope="module")
def deployed_microceph(juju: jubilant.Juju, microceph_charm: Path) -> str:
    """Deploy a single-unit MicroCeph cluster (no OSDs needed for this test)."""
    helpers.deploy_microceph(juju, microceph_charm, APP_NAME, timeout=1500)
    return APP_NAME


@pytest.mark.abort_on_fail
def test_nfs_binding_round_trip(juju: jubilant.Juju, deployed_microceph: str) -> None:
    """nfs-address tracks nfs-use-dedicated-binding against the real binding."""
    unit = helpers.first_unit_name(juju.status(), APP_NAME)

    # Default: option disabled -> public-address published, nfs-address absent.
    data = _peers_self_data(juju, unit)
    logger.info("Default peers data for %s: %s", unit, data)
    assert data.get("public-address"), "expected a public-address to be published by default"
    assert "nfs-address" not in data, f"nfs-address must not be published by default: {data}"

    # Opt in: NFS follows the nfs extra-binding.
    juju.config(APP_NAME, {"nfs-use-dedicated-binding": "true"})
    data = _wait_for_peers_data(
        juju,
        unit,
        lambda d: bool(d.get("nfs-address")),
        description="publish nfs-address",
    )
    logger.info("Peers data with nfs-use-dedicated-binding=true: %s", data)
    nfs_address = data["nfs-address"]
    # The published value must be a real IP resolved from the actual binding.
    ipaddress.ip_address(nfs_address)
    # On a single-network LXD model the nfs binding resolves to the same
    # address as the public binding; both must be real addresses.
    assert nfs_address == data.get("public-address"), (
        "nfs-address should match the resolved public binding address on a "
        f"single-network model: {data}"
    )

    # Opt back out: the stale nfs-address must be cleared.
    juju.config(APP_NAME, {"nfs-use-dedicated-binding": "false"})
    data = _wait_for_peers_data(
        juju,
        unit,
        lambda d: not d.get("nfs-address"),
        description="clear nfs-address",
    )
    logger.info("Peers data after disabling nfs-use-dedicated-binding: %s", data)
    assert not data.get("nfs-address"), f"nfs-address should be cleared once disabled: {data}"
    assert data.get("public-address"), "public-address must remain after disabling the option"
