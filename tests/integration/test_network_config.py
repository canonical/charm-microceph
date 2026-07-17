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

"""Integration tests for the configurable network options (spec CE147).

``ceph-public-network`` and ``ceph-cluster-network`` let the operator hand the
charm the full subnet list a deployment spans, instead of the charm
auto-selecting the single subnet the leader happens to sit on:

* ``ceph-public-network`` is consumed exactly once, at cluster bootstrap
  (``microceph cluster bootstrap --public-network``). microceph marks
  ``public_network`` read-only on a running cluster, so the charm blocks every
  unit when the option changes after bootstrap; reverting the option (any
  spelling of the same subnet set) recovers the application.
* ``ceph-cluster-network`` is applied at bootstrap and reconciled on every
  config change. Clearing it after it was set reverts the cluster network to
  the leader's subnet in the ``cluster`` space - the same fallback used when
  the option was never set.

How these tests observe the live cluster
-----------------------------------------
microceph applies both values with ``ceph config set global <key> <value>``
(at bootstrap via ``BootstrapCephConfigs``, at runtime via its cluster-config
API), so ``ceph config get mon <key>`` on any unit returns exactly the string
microceph applied. That is the read-back used throughout: it proves the
operator's value reached Ceph itself, not merely the charm's own bookkeeping.

Why the machines' subnet is split into halves
---------------------------------------------
On a single-network model the charm's space-subnet fallback resolves to the
machines' provider subnet (for example ``10.x.y.0/24``), so configuring that
same ``/24`` would be indistinguishable from the charm silently ignoring the
option. Instead, the fixtures split the provider subnet into its two ``/25``
halves and configure those. That yields:

* values the fallback could never produce, so every read-back below proves the
  configured value was really consumed;
* a genuine multi-subnet, mixed-delimiter list - the exact input shape CE147
  adds (microceph accepts a unit as long as it holds an address on ANY listed
  subnet, and every machine here holds an address on one of the halves);
* observable order preservation - subnet order is an address-selection
  priority in microceph (``FindIpOnSubnet`` is first-match-wins), so the charm
  must forward the list exactly as the operator wrote it, only canonicalised
  to comma-separated form.

Test ordering
-------------
The module deploys one three-unit cluster (LXD containers; OSDs are not needed
to exercise network configuration) and the tests walk one dependent story in
file order:

1. bootstrap consumes both options;
2. a public-network change blocks every unit and a revert recovers;
3. a malformed value blocks and a revert recovers;
4. a cluster-network change is applied to the running cluster;
5. clearing the cluster network reverts it to the space fallback.

Each test returns the model to the healthy state the next test expects, so a
failure points at the step that actually broke.

The deploy pins ``snap-channel`` to the first microceph release able to parse
multi-subnet network flags (see ``SNAP_CHANNEL`` below).
"""

import ipaddress
import json
import logging
import time
from pathlib import Path
from typing import NamedTuple

import jubilant
import pytest
import yaml

from tests import helpers

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
PUBLIC_OPTION = "ceph-public-network"
CLUSTER_OPTION = "ceph-cluster-network"
NUM_UNITS = 3
# Must match the base of the charm artifact the microceph_charm fixture
# resolves (microceph_ubuntu-24.04-amd64.charm).
MACHINE_BASE = "ubuntu@24.04"
SNAP_CHANNEL = "tentacle/stable"

MACHINES_TIMEOUT = 900
DEPLOY_TIMEOUT = 2400
BLOCK_TIMEOUT = 600
RECOVER_TIMEOUT = 900
CONFIG_APPLY_TIMEOUT = 600
QUORUM_TIMEOUT = 300


class NetworkLayout(NamedTuple):
    """The addressing plan derived from the machines' provider subnet.

    ``public_config``/``cluster_config`` are the option values as an operator
    would type them (mixed delimiters, cluster list deliberately reversed);
    the ``*_canonical`` fields are the comma-joined forms the charm forwards
    to microceph and therefore what ``ceph config get`` must return.
    """

    machines: list[str]
    subnet: ipaddress.IPv4Network
    halves: tuple[ipaddress.IPv4Network, ipaddress.IPv4Network]
    public_config: str
    public_canonical: str
    cluster_config: str
    cluster_canonical: str


def _ipv4_addresses(machine: jubilant.statustypes.MachineStatus) -> list[ipaddress.IPv4Address]:
    """Return the machine's IPv4 addresses, ignoring IPv6 and unparsable entries."""
    addresses = []
    for raw in machine.ip_addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if address.version == 4:
            addresses.append(address)
    return addresses


def _wait_for_started_machines(
    juju: jubilant.Juju, count: int, *, timeout: int = MACHINES_TIMEOUT, interval: int = 10
) -> dict[str, list[ipaddress.IPv4Address]]:
    """Wait until *count* machines are started with an IPv4 address each.

    The machines' addresses must exist before the app is deployed because the
    network options are computed from them and must be present at deploy time
    (bootstrap consumes ``ceph-public-network`` and cannot retrofit it).
    """
    deadline = time.time() + timeout
    machines: dict[str, list[ipaddress.IPv4Address]] = {}
    while time.time() < deadline:
        status = juju.status()
        # Fail fast on a machine stuck in an explicit failure state rather
        # than burning the whole timeout on it.
        broken = {
            name: machine.machine_status.message or machine.juju_status.current
            for name, machine in status.machines.items()
            if "failed" in (machine.juju_status.current, machine.machine_status.current)
            or machine.machine_status.current == "error"
        }
        if broken:
            raise AssertionError(f"machine provisioning failed: {broken}")
        machines = {
            name: _ipv4_addresses(machine)
            for name, machine in status.machines.items()
            if machine.juju_status.current == "started"
        }
        ready = {name: addrs for name, addrs in machines.items() if addrs}
        if len(ready) >= count:
            return ready
        time.sleep(interval)
    raise AssertionError(
        f"Timed out waiting for {count} started machines with IPv4 addresses; last: {machines}"
    )


def _provider_ipv4_subnets(juju: jubilant.Juju) -> list[ipaddress.IPv4Network]:
    """Return the model's known IPv4 subnets, with their true prefix lengths.

    ``juju subnets`` reports the provider CIDRs, so the layout does not have to
    assume the substrate uses a /24.
    """
    raw = json.loads(juju.cli("subnets", "--format", "json"))
    subnets = []
    for cidr, info in raw.get("subnets", {}).items():
        if info.get("type") == "ipv4":
            subnets.append(ipaddress.ip_network(cidr))
    return subnets


def _shared_subnet(
    subnets: list[ipaddress.IPv4Network],
    machines: dict[str, list[ipaddress.IPv4Address]],
) -> ipaddress.IPv4Network:
    """Return the one subnet the test machines are actually on.

    ``juju subnets`` reports every network the host knows about, not just the
    one containers attach to. On a typical LXD host that is three or more
    entries, for example::

        10.141.192.0/24   lxdbr0    <- the containers actually live here
        10.56.203.0/24    mpqemubr0 (multipass bridge)
        172.17.0.0/16     docker0

    The machines' own addresses tell the candidates apart: a subnet is kept
    only if EVERY machine has at least one address inside it. In the example,
    no machine has an address on the multipass or docker bridges, so only the
    lxdbr0 subnet survives.

    If nothing survives (the provider scattered the machines across different
    bridges), no meaningful layout can be built, so fail right away with the
    evidence instead of deploying and failing confusingly later.

    More than one subnet can survive only when the reported subnets nest, for
    example both 10.141.0.0/16 and 10.141.192.0/24: every machine is "on"
    both. In that case pick the narrowest (largest prefix length, /24 over
    /16), because that is the subnet Juju reports on the unit's own network
    binding - and therefore exactly what the charm's space fallback resolves
    to. The clear-reverts-to-fallback test compares against this value, so
    the layout's notion of "the subnet" must match the charm's. The network
    address is only an arbitrary-but-deterministic tiebreak. On a plain
    single-bridge setup exactly one subnet survives and the sort changes
    nothing; the log line makes the rarer nested case visible.
    """
    shared = [
        subnet
        for subnet in subnets
        # "does every machine have an address on this subnet?"
        if all(any(addr in subnet for addr in addrs) for addrs in machines.values())
    ]
    if not shared:
        raise AssertionError(
            f"no provider subnet contains an address of every machine; "
            f"subnets={subnets} machines={machines}"
        )
    # Narrowest subnet first: -prefixlen sorts /24 before /16.
    shared.sort(key=lambda net: (-net.prefixlen, net.network_address))
    if len(shared) > 1:
        logger.info("Multiple shared subnets %s; using most specific %s", shared, shared[0])
    return shared[0]


def _ceph_config_get(juju: jubilant.Juju, unit: str, key: str) -> str:
    """Read *key* from Ceph's central config store on *unit*.

    microceph applies both network options with ``ceph config set global``,
    and the ``global`` section is inherited by every daemon type, so asking
    the ``mon`` entity returns the value verbatim. An unset key reads back as
    an empty string.
    """
    return juju.ssh(unit, "sudo", "microceph.ceph", "config", "get", "mon", key).strip()


def _wait_for_ceph_config(
    juju: jubilant.Juju,
    unit: str,
    key: str,
    expected: str,
    *,
    timeout: int = CONFIG_APPLY_TIMEOUT,
    interval: int = 10,
) -> None:
    """Poll Ceph's config store until *key* reads back as *expected*.

    The charm applies cluster-network changes inside the config-changed hook,
    but hook scheduling is asynchronous with the test process, so read-backs
    poll instead of asserting immediately. The poll fails fast if the app
    reports an explicit failure state (blocked/error) instead of burning the
    whole timeout on a change that will never be applied.
    """
    deadline = time.time() + timeout
    last = "<unread>"
    while time.time() < deadline:
        reasons = helpers.status_failure_reasons(juju.status(), APP_NAME)
        if reasons:
            raise AssertionError(f"aborting wait for {key}={expected!r}: {'; '.join(reasons)}")
        try:
            last = _ceph_config_get(juju, unit, key)
        except jubilant.CLIError as exc:
            # A transient ssh failure must not fail the wait: keep polling.
            logger.info("reading %s failed (%s); retrying", key, exc)
        else:
            if last == expected:
                return
        time.sleep(interval)
    raise AssertionError(f"{key} never became {expected!r}; last read-back: {last!r}")


def _wait_for_all_units_blocked(
    juju: jubilant.Juju, app: str, *fragments: str, timeout: int = BLOCK_TIMEOUT
) -> None:
    """Wait until every unit of *app* is blocked with all *fragments* in its message.

    The config checks run on every unit (not only the leader), so a violation
    must block the whole application - asserting on all units pins exactly
    that behavior.
    """

    def _blocked(status: jubilant.Status) -> bool:
        units = status.apps[app].units
        return bool(units) and all(
            unit.workload_status.current == "blocked"
            and all(fragment in (unit.workload_status.message or "") for fragment in fragments)
            for unit in units.values()
        )

    with helpers.fast_forward(juju):
        juju.wait(_blocked, error=jubilant.any_error, timeout=timeout)


def _charm_hook_error(status: jubilant.Status) -> bool:
    """True when a unit reports an unhandled charm exception.

    A charm bug surfaces as blocked "Error in charm (see logs)" with the agent
    idle, which neither ``jubilant.any_error`` nor an active-wait would catch
    until the full timeout expired. Never true for the charm's intentional
    blocked states, whose messages name the offending config instead.
    """
    app = status.apps.get(APP_NAME)
    return bool(app) and any(
        unit.workload_status.current == "blocked"
        and "Error in charm" in (unit.workload_status.message or "")
        for unit in app.units.values()
    )


def _wait_for_active(juju: jubilant.Juju, timeout: int) -> None:
    """Wait for every unit to reach active, failing fast on charm errors."""
    with helpers.fast_forward(juju):
        juju.wait(
            lambda status: jubilant.all_active(status, APP_NAME),
            error=lambda status: jubilant.any_error(status) or _charm_hook_error(status),
            timeout=timeout,
        )


def _wait_for_recovery(juju: jubilant.Juju) -> None:
    """Wait for the application to reconcile back to active on every unit."""
    _wait_for_active(juju, RECOVER_TIMEOUT)


def _wait_for_mon_quorum(
    juju: jubilant.Juju, expected: int, *, timeout: int = QUORUM_TIMEOUT, interval: int = 15
) -> None:
    """Wait until *expected* monitors are in quorum.

    Units join the cluster (and enable their mon) before the charm settles, but
    quorum membership is reported asynchronously; poll briefly, failing fast if
    the app falls into an explicit failure state (blocked/error) meanwhile.
    """
    deadline = time.time() + timeout
    quorum: list = []
    while time.time() < deadline:
        reasons = helpers.status_failure_reasons(juju.status(), APP_NAME)
        if reasons:
            raise AssertionError(f"aborting wait for mon quorum: {'; '.join(reasons)}")
        ceph_status = helpers.fetch_ceph_status(juju, APP_NAME)
        quorum = ceph_status.get("quorum_names") or []
        if len(quorum) >= expected:
            return
        time.sleep(interval)
    raise AssertionError(f"expected {expected} mons in quorum, got {quorum}")


@pytest.fixture(scope="module")
def network_layout(juju: jubilant.Juju) -> NetworkLayout:
    """Compute the addressing plan from freshly provisioned machines.

    Machines are added before the deploy because ``ceph-public-network`` only
    applies at bootstrap: the option value must exist when ``juju deploy``
    runs, and it can only be derived from addresses the substrate actually
    assigned. The machines' shared provider subnet is split into its two
    halves; every machine necessarily holds an address on exactly one half, so
    the halves form a valid multi-subnet configuration for both options.
    """
    logger.info("Provisioning %d machines to discover the provider subnet", NUM_UNITS)
    juju.add_machine(base=MACHINE_BASE, num_machines=NUM_UNITS)
    machines = _wait_for_started_machines(juju, NUM_UNITS)

    subnet = _shared_subnet(_provider_ipv4_subnets(juju), machines)
    if subnet.prefixlen > 28:
        raise AssertionError(f"provider subnet {subnet} is too small to split into halves")
    lower, upper = subnet.subnets(prefixlen_diff=1)

    layout = NetworkLayout(
        machines=sorted(machines, key=int),
        subnet=subnet,
        halves=(lower, upper),
        # The operator's spelling: comma-delimited for the public network,
        # space-delimited and deliberately in reversed order for the cluster
        # network. Both delimiters are supported, and the order must survive
        # into microceph because it is an address-selection priority there.
        public_config=f"{lower},{upper}",
        public_canonical=f"{lower},{upper}",
        cluster_config=f"{upper} {lower}",
        cluster_canonical=f"{upper},{lower}",
    )
    logger.info(
        "Machines %s on %s; halves %s; per-machine addresses %s",
        layout.machines,
        subnet,
        layout.halves,
        machines,
    )
    return layout


@pytest.fixture(scope="module")
def network_cluster(
    juju: jubilant.Juju, microceph_charm: Path, network_layout: NetworkLayout
) -> NetworkLayout:
    """Deploy microceph onto the pre-provisioned machines with both options set."""
    logger.info(
        "Deploying %s with %s=%r %s=%r",
        APP_NAME,
        PUBLIC_OPTION,
        network_layout.public_config,
        CLUSTER_OPTION,
        network_layout.cluster_config,
    )
    juju.deploy(
        str(microceph_charm),
        APP_NAME,
        num_units=NUM_UNITS,
        to=network_layout.machines,
        config={
            "snap-channel": SNAP_CHANNEL,
            PUBLIC_OPTION: network_layout.public_config,
            CLUSTER_OPTION: network_layout.cluster_config,
        },
    )
    _wait_for_active(juju, DEPLOY_TIMEOUT)
    return network_layout


@pytest.mark.abort_on_fail
@pytest.mark.smoke
def test_bootstrap_applies_configured_networks(
    juju: jubilant.Juju, network_cluster: NetworkLayout
) -> None:
    """Bootstrap must consume both options and hand them to Ceph verbatim.

    The values read back from Ceph's config store are the split halves, which
    the space-subnet fallback could never produce, so a match proves the
    config flowed charm -> microceph bootstrap -> Ceph. The read-back must
    also preserve the operator's subnet order (only canonicalised to commas):
    order is an address-selection priority in microceph. Finally, all three
    mons must reach quorum - every unit joined a cluster whose public network
    is the multi-subnet list, and microceph validates each joiner holds an
    address on one of the listed subnets.
    """
    layout = network_cluster
    status = juju.status()
    assert jubilant.all_active(status, APP_NAME)

    unit = helpers.first_unit_name(status, APP_NAME)
    assert _ceph_config_get(juju, unit, "public_network") == layout.public_canonical
    assert _ceph_config_get(juju, unit, "cluster_network") == layout.cluster_canonical

    _wait_for_mon_quorum(juju, NUM_UNITS)


@pytest.mark.smoke
def test_public_network_change_after_bootstrap_blocks(
    juju: jubilant.Juju, network_cluster: NetworkLayout
) -> None:
    """A post-bootstrap public-network change blocks; reverting recovers.

    microceph cannot apply a new ``public_network`` to a running cluster, so
    the only honest behaviors are to block (change) or stay active (no
    change). This test pins four properties:

    * every unit blocks, not only the leader (the check runs everywhere);
    * the blocked message names the recorded value, which is the operator's
      only way out;
    * the live cluster keeps the bootstrap value while blocked;
    * reverting recovers even when the revert respells the same subnet set
      (reordered, different delimiters) - comparison is on the canonical
      parsed set, not the raw string.
    """
    layout = network_cluster
    unit = helpers.first_unit_name(juju.status(), APP_NAME)

    # TEST-NET-1 (RFC 5737): guaranteed not to collide with the substrate.
    foreign = "192.0.2.0/24"
    assert not layout.subnet.overlaps(ipaddress.ip_network(foreign))

    juju.config(APP_NAME, {PUBLIC_OPTION: foreign})
    _wait_for_all_units_blocked(
        juju,
        APP_NAME,
        f"{PUBLIC_OPTION} cannot be changed after bootstrap",
        layout.public_canonical,
    )
    assert _ceph_config_get(juju, unit, "public_network") == layout.public_canonical

    # Same subnet set, respelled: reversed order, comma+space delimiters and
    # surrounding whitespace. This must count as "unchanged" and recover.
    respelled = f" {layout.halves[1]}, {layout.halves[0]} "
    juju.config(APP_NAME, {PUBLIC_OPTION: respelled})
    _wait_for_recovery(juju)
    assert _ceph_config_get(juju, unit, "public_network") == layout.public_canonical


@pytest.mark.smoke
def test_invalid_network_value_blocks_and_recovers(
    juju: jubilant.Juju, network_cluster: NetworkLayout
) -> None:
    """A malformed subnet blocks the application and names the bad entry.

    Validation must reject the whole value rather than silently dropping the
    bad entry: a typo that half-applied would leave the cluster on a subset of
    the intended networks. The message names the offending token so the
    operator can fix exactly that. Reverting to the previously applied value
    recovers without touching the running cluster (the charm's reconcile diffs
    against what is already applied).
    """
    layout = network_cluster
    unit = helpers.first_unit_name(juju.status(), APP_NAME)

    juju.config(APP_NAME, {CLUSTER_OPTION: f"{layout.halves[0]} bogus/24"})
    _wait_for_all_units_blocked(juju, APP_NAME, f"Invalid config {CLUSTER_OPTION}", "bogus/24")
    # The rejected value must not have leaked into the running cluster.
    assert _ceph_config_get(juju, unit, "cluster_network") == layout.cluster_canonical

    juju.config(APP_NAME, {CLUSTER_OPTION: layout.cluster_config})
    _wait_for_recovery(juju)
    assert _ceph_config_get(juju, unit, "cluster_network") == layout.cluster_canonical


@pytest.mark.smoke
def test_cluster_network_reconfigure_applies_live(
    juju: jubilant.Juju, network_cluster: NetworkLayout
) -> None:
    """Changing ceph-cluster-network reconfigures the running cluster.

    Unlike the public network, the cluster network is mutable after bootstrap:
    the leader reconciles it on config-changed through microceph's
    cluster-config API (which is what restarts the affected daemons - the
    "momentary service disruption" the option's description warns about; no
    OSDs are deployed here so the restart set is empty). Narrowing from the
    bootstrap-time two-subnet list to a single half is a real value change and
    must reach Ceph's config store.

    Note microceph does not re-validate subnet membership on runtime changes
    (only at bootstrap/join), so this passes regardless of which half the
    containers' addresses landed on.
    """
    layout = network_cluster
    unit = helpers.first_unit_name(juju.status(), APP_NAME)

    target = str(layout.halves[0])
    assert target != layout.cluster_canonical
    juju.config(APP_NAME, {CLUSTER_OPTION: target})

    _wait_for_recovery(juju)
    _wait_for_ceph_config(juju, unit, "cluster_network", target)


@pytest.mark.smoke
def test_cluster_network_clear_reverts_to_space_fallback(
    juju: jubilant.Juju, network_cluster: NetworkLayout
) -> None:
    """Clearing ceph-cluster-network reverts to the cluster-space subnet.

    "Cleared after being set" must be distinguishable from "never set": the
    charm tracks the last value it applied, and on clearing reverts the
    cluster network to the leader's subnet in the ``cluster`` space instead of
    leaving the operator's last custom value in place. On this single-network
    model that fallback is the machines' full provider subnet - a value
    different from the half applied by the previous test, so the read-back
    proves the revert really happened rather than nothing at all.
    """
    layout = network_cluster
    unit = helpers.first_unit_name(juju.status(), APP_NAME)

    fallback = str(layout.subnet)
    assert fallback != str(layout.halves[0])
    juju.config(APP_NAME, {CLUSTER_OPTION: ""})

    _wait_for_recovery(juju)
    _wait_for_ceph_config(juju, unit, "cluster_network", fallback)
