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

"""Regression test: microceph must advertise ALL mons to its ceph clients.

Reproduces the bug where a client (e.g. cinder-volume) ends up with a single
``mon host``. On the provider side this shows up as:

* only the Ceph mon-leader unit publishing its per-unit ``ceph-public-address``
  (so the client fallback sees one mon), and
* the application-level ``ceph-mon-public-addresses`` list being published only
  when the Juju leader and the Ceph mon leader happen to be the same unit.
"""

import json
import logging
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from tests import helpers

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
CEPHCLIENT_APP = "cephclient"
NUM_UNITS = 3
LOOP_OSD_SPEC = "1G,3"
TESTER_CHARM = (
    Path(__file__).parent / "testers/cephclient/cephclient-requirer-mock.charm"
).resolve()

# How long to wait for every mon unit to advertise its address after integrate.
PUBLISH_TIMEOUT = 360
PUBLISH_INTERVAL = 15


@pytest.fixture(scope="module")
def integrated_cluster(juju_vm, microceph_charm):
    """Deploy 3 microceph units + the ceph client tester and integrate them."""
    juju = juju_vm
    logger.info("Deploying %s (-n%d) on VMs", APP_NAME, NUM_UNITS)
    juju.deploy(str(microceph_charm), APP_NAME, num_units=NUM_UNITS)
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, timeout=2400)

    helpers.ensure_loop_osd(juju, APP_NAME, LOOP_OSD_SPEC)

    logger.info("Deploying ceph client tester %s", TESTER_CHARM)
    juju.deploy(str(TESTER_CHARM), CEPHCLIENT_APP)
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=2400)

    juju.integrate(f"{CEPHCLIENT_APP}:ceph", f"{APP_NAME}:ceph")
    with helpers.fast_forward(juju):
        helpers.wait_for_apps(juju, APP_NAME, CEPHCLIENT_APP, timeout=1200)
    return juju


def _first_json(text: str):
    """Parse the first JSON value in *text*, ignoring surrounding noise.

    ``juju ssh`` can fold the remote command's stderr into its output (e.g.
    ``microceph.ceph mon dump`` prints ``dumped monmap epoch N``, and ssh may
    append ``Connection to <ip> closed.``), which would otherwise make
    ``json.loads`` raise "Extra data". Skip to the first ``{``/``[`` and decode
    just that value.
    """
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError(f"no JSON found in output: {text!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _provider_units(juju) -> list:
    """Return the sorted microceph unit names."""
    return sorted(juju.status().apps[APP_NAME].units)


def _mon_count(juju) -> int:
    """Return how many monitors the live cluster reports."""
    unit = helpers.first_unit_name(juju.status(), APP_NAME)
    out = juju.ssh(unit, "sudo", "microceph.ceph", "mon", "dump", "--format", "json")
    return len(_first_json(out).get("mons", []))


def _units_publishing_address(juju, client_unit) -> dict:
    """Map each microceph unit to the ceph-public-address it advertises.

    Read from the client's perspective: provider units appear as related-units
    in the client's ceph relation data.
    """
    model = juju.status().model.name
    published = {}
    for unit in _provider_units(juju):
        data = helpers.get_relation_data(client_unit, "ceph", unit, model)
        published[unit] = data.get("ceph-public-address")
    return published


def _juju_leader(juju) -> str:
    """Return the microceph Juju leader unit name (falls back to unit 0)."""
    model = juju.status().model.name
    for unit in _provider_units(juju):
        if helpers.get_unit_info(unit, model).get("leader"):
            return unit
    return f"{APP_NAME}/0"


def _app_mon_addresses(juju) -> list:
    """Return microceph's app-level ceph-mon-public-addresses list (or []).

    Read via ``relation-get --app`` from the leader, the authoritative view of
    the provider's own application databag. The leader is resolved from
    ``juju status`` (controller-side) so it stays correct even while another
    unit's agent is intentionally stopped.
    """
    model = juju.status().model.name
    leader = _leader_of(_microceph_units(model)) or f"{APP_NAME}/0"
    rel = subprocess.run(
        ["juju", "exec", "-m", model, "--unit", leader, "--", "relation-ids", "ceph"],
        capture_output=True,
        text=True,
    )
    if rel.returncode != 0:
        logger.warning("relation-ids ceph failed: %s", rel.stderr.strip())
        return []
    relids = rel.stdout.split()
    if not relids:
        return []
    got = subprocess.run(
        # fmt: off
        [
            "juju", "exec", "-m", model, "--unit", leader, "--",
            "relation-get", "--app", "-r", relids[0], "-", leader,
        ],
        # fmt: on
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        logger.warning("relation-get --app failed: %s", got.stderr.strip())
        return []
    data = yaml.safe_load(got.stdout) or {}
    raw = data.get("ceph-mon-public-addresses")
    return json.loads(raw) if raw else []


def _log_leadership(juju) -> None:
    """Log which unit is the Juju leader vs the Ceph mon leader (diagnostic).

    The bug only manifests when these differ; logging it makes the run's
    conditions visible.
    """
    juju_leader = _juju_leader(juju)
    mon_leader = None
    for unit in _provider_units(juju):
        try:
            out = juju.ssh(
                unit,
                "sudo",
                "microceph.ceph",
                "tell",
                "mon.$(hostname)",
                "mon_status",
                "--format",
                "json",
            )
            if _first_json(out).get("state") == "leader":
                mon_leader = unit
        except Exception as exc:  # diagnostic only; never fail the test here
            logger.debug("mon_status probe failed on %s: %s", unit, exc)
    logger.info(
        "Juju leader=%s  Ceph mon leader=%s  coincide=%s",
        juju_leader,
        mon_leader,
        juju_leader == mon_leader,
    )


def _microceph_units(model: str) -> dict:
    """Return the microceph units block from ``juju status`` JSON.

    Read via the controller, so it stays usable while a unit agent is
    intentionally stopped.
    """
    out = subprocess.run(
        ["juju", "status", "-m", model, APP_NAME, "--format", "json"],
        capture_output=True,
        text=True,
    ).stdout
    apps = json.loads(out).get("applications", {}) if out else {}
    return (apps.get(APP_NAME) or {}).get("units", {}) or {}


def _leader_of(units: dict) -> str:
    """Return the leader unit name from a units block, or '' if none."""
    for name, unit in units.items():
        if unit.get("leader"):
            return name
    return ""


@pytest.mark.smoke
def test_all_mons_advertised_to_client(integrated_cluster):
    """Every mon unit must advertise its address; clients must see all mons."""
    juju = integrated_cluster
    client_unit = helpers.first_unit_name(juju.status(), CEPHCLIENT_APP)

    expected = _mon_count(juju)
    # All deployed units should be mons; if not, fail fast with a clear message
    # rather than silently breaking the per-unit discriminator below.
    assert (
        expected == NUM_UNITS
    ), f"expected {NUM_UNITS} mons (one per unit), monmap reports {expected}"
    _log_leadership(juju)

    # Poll until every mon unit advertises its own ceph-public-address AND the
    # app-level list enumerates every mon. With the bug only the mon-leader unit
    # advertises its address (count stays 1); once fixed all mon units do.
    published, app_addrs = {}, []
    deadline = time.time() + PUBLISH_TIMEOUT
    with helpers.fast_forward(juju):
        while time.time() < deadline:
            published = _units_publishing_address(juju, client_unit)
            app_addrs = _app_mon_addresses(juju)
            advertised = {u: a for u, a in published.items() if a}
            if len(advertised) == expected and len(app_addrs) == expected:
                break
            logger.info(
                "waiting: per-unit=%d/%d app=%d/%d  per-unit=%s",
                len(advertised),
                expected,
                len(app_addrs),
                expected,
                published,
            )
            time.sleep(PUBLISH_INTERVAL)

    advertised = {u: a for u, a in published.items() if a}
    assert len(advertised) == expected, (
        f"expected all {expected} mon units to advertise ceph-public-address, "
        f"got {len(advertised)}: {published}"
    )
    # Addresses must be distinct (each unit its own mon address).
    assert len(set(advertised.values())) == expected, f"duplicate addresses: {advertised}"
    # The authoritative app-level list must also enumerate every mon.
    assert (
        len(app_addrs) == expected
    ), f"app ceph-mon-public-addresses should list {expected} mons, got {app_addrs}"


@pytest.mark.regression
def test_app_list_published_when_leaders_differ(integrated_cluster):
    """Force Juju leader != Ceph mon leader and confirm the app list survives.

    This is the exact bug-triggering condition: pre-fix, when these two leaders
    were different units, the full mon list was never published on a fresh
    deploy. We move Juju leadership off the mon-leader unit by bouncing that
    unit's machine agent (its ceph mon stays up, so it remains the Ceph mon
    leader), clear the published list, and assert the new non-mon-leader Juju
    leader republishes the full list.
    """
    juju = integrated_cluster
    model = juju.status().model.name
    expected = _mon_count(juju)

    units = _microceph_units(model)
    old_leader = _leader_of(units)
    assert old_leader, "no Juju leader found"
    agent = f"jujud-machine-{units[old_leader]['machine']}"

    # Bounce the leader's agent so leadership moves to a peon unit. Its ceph mon
    # is untouched, so old_leader stays the Ceph mon leader. Keep the agent
    # stopped through the entire check so old_leader cannot reclaim Juju
    # leadership mid-test (which would silently restore J == M and let the test
    # pass for the wrong reason). All operations below target the new leader
    # (agent up) and the controller, so they work while old_leader is down.
    juju.ssh(old_leader, "sudo", "systemctl", "stop", agent)
    try:
        new_leader = ""
        deadline = time.time() + 300
        while time.time() < deadline:
            new_leader = _leader_of(_microceph_units(model))
            if new_leader and new_leader != old_leader:
                break
            new_leader = ""
            time.sleep(10)
        assert new_leader, f"Juju leadership did not move off {old_leader}"
        logger.info(
            "Juju leader moved %s -> %s; Ceph mon leader stays on %s",
            old_leader,
            new_leader,
            old_leader,
        )

        with helpers.fast_forward(juju):
            # Clear the published app list, then confirm the J!=M leader rewrites it.
            relids = subprocess.run(
                ["juju", "exec", "-m", model, "--unit", new_leader, "--", "relation-ids", "ceph"],
                capture_output=True,
                text=True,
            ).stdout.split()
            assert relids, f"no ceph relation-id visible on {new_leader}"
            subprocess.run(
                # fmt: off
                [
                    "juju", "exec", "-m", model, "--unit", new_leader, "--",
                    "relation-set", "--app", "-r", relids[0], "ceph-mon-public-addresses=",
                ],
                # fmt: on
                check=True,
                capture_output=True,
                text=True,
            )

            app_addrs = []
            deadline = time.time() + 240
            while time.time() < deadline:
                app_addrs = _app_mon_addresses(juju)
                if len(app_addrs) == expected:
                    break
                logger.info(
                    "waiting for republish by %s: app=%d/%d", new_leader, len(app_addrs), expected
                )
                time.sleep(15)

        assert len(app_addrs) == expected, (
            f"non-mon-leader Juju leader {new_leader} did not republish the full mon "
            f"list; got {app_addrs}"
        )
    finally:
        # Always bring the bounced agent back, even on assertion failure, and
        # never let a restart hiccup mask the real test result.
        for _ in range(5):
            try:
                juju.ssh(old_leader, "sudo", "systemctl", "start", agent)
                break
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.warning("retrying agent restart on %s: %s", old_leader, exc)
                time.sleep(5)
