#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utils module."""

import ipaddress
import logging
import subprocess

import requests

import microceph
from microceph_client import Client

logger = logging.getLogger(__name__)


def _normalize_ip(addr: str) -> str:
    """Return the canonical form of a bare IP, or the input unchanged."""
    try:
        return str(ipaddress.ip_address(addr))
    except ValueError:
        return addr


def run_cmd(cmd: list, timeout: int = 180) -> str:
    """Execute provided command via subprocess."""
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        logger.debug(f"Command {' '.join(cmd)} finished; Output: {process.stdout}")
        return process.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed executing cmd: {cmd}, error: {e.stderr}")
        raise e


def run_cmd_with_input(cmd: list, input_data: str) -> str:
    """Execute provided command with input to stdin."""
    try:
        output = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        logger.debug(f"Command {' '.join(cmd)} finished; Output: {output.stdout}")
        return output.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed executing cmd: {cmd}, error: {e.stderr}")
        raise e


def snap_has_connection(snap_name: str, plug_or_slot: str) -> bool:
    """Check if a snap has a specific connection.

    :param snap_name: Snap to check connection for
    :param plug_or_slot: Plug or slot to check for connection
    """
    cmd = ["snap", "run", "--shell", snap_name, "-c", f"snapctl is-connected {plug_or_slot}"]
    logger.debug("Checking snap connection: %s %s", snap_name, plug_or_slot)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    if result.returncode == 1 and not result.stderr.strip():
        return False
    logger.error(f"Failed executing cmd: {cmd}, error: {result.stderr}")
    raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)


def is_departing(app, context: str = "") -> bool:
    """Return True when the application is being removed.

    ``planned_units()`` is the application's goal unit count, which Juju drives
    to 0 on ``remove-application`` (and scale-to-zero). On any failure to read
    it we fail safe to False so the unit keeps reconciling normally. ``context``
    is an optional caller hint included in the warning log line.
    """
    try:
        return app.planned_units() == 0
    except Exception as e:
        suffix = f" during {context}" if context else ""
        logger.warning("Could not determine planned units%s: %s", suffix, e)
        return False


def _sort_mon_addresses(addrs: list[str]) -> list[str]:
    """Return mon addresses in a stable, deterministic order.

    The microceph service API (and the ceph.conf fallback) can report the same
    set of mons in a different order on successive calls. Publishing that list
    verbatim makes an otherwise unchanged mon set serialize differently each
    time, which Juju treats as a real relation-data change and fans out to every
    client (LP#2161602). Sort by normalized IP so equivalent representations
    order consistently, with the original string as a tiebreaker.
    """
    return sorted(addrs, key=lambda a: (_normalize_ip(a) or a, a))


def get_mon_addresses():
    """Get the Ceph mon addresses, cross-checked against the live monmap.

    The microceph service API is not refreshed when a mon leaves the cluster
    out-of-band, so it can report dead mons. Filter the reported addresses to
    those present in the live monmap (``ceph mon dump``). Fall back to the
    unfiltered list if the monmap is unavailable or the intersection is empty,
    so a format mismatch can never regress to an empty list.

    The returned list is sorted in a stable order so that an unchanged set of
    mons always serializes identically; this prevents mere reordering from
    triggering spurious relation-changed events (LP#2161602).
    """
    # Local import: ceph imports utils, so a module-level import would cycle.
    import ceph

    client = Client.from_socket()
    try:
        addrs = client.cluster.get_mon_addresses()
    except requests.HTTPError:
        # The /1.0/services/mon endpoint is newer than some microceph snap
        # channels the charm can deploy; on an older snap it returns 404 (a bare
        # HTTPError - "daemon/db not yet initialized" and a missing socket are
        # raised as ClusterServiceUnavailableException, not caught here). Parse
        # ceph.conf instead, which carries the mon host list on every version.
        logger.debug("Mon api call failed, fall back to legacy method")
        addrs = microceph.get_mon_public_addresses()

    live = ceph.get_live_mon_ips()
    if addrs and live:
        filtered = [a for a in addrs if _normalize_ip(a) in live]
        if filtered:
            logger.debug(
                "get_mon_addresses: cross-check kept %s of %s reported mons (live monmap: %s)",
                filtered,
                addrs,
                sorted(live),
            )
            return _sort_mon_addresses(filtered)
        logger.warning(
            "Live monmap cross-check removed all reported mon addresses; "
            "using the unfiltered list %s",
            addrs,
        )
    elif addrs:
        logger.debug(
            "get_mon_addresses: live monmap unavailable (%s); publishing unfiltered list %s",
            live or "empty",
            addrs,
        )
    return _sort_mon_addresses(addrs)


def get_fsid():
    """Get the FSID from ceph.conf."""
    with open("/var/snap/microceph/current/conf/ceph.conf", "r") as f:
        for line in f:
            if line.startswith("fsid") and "=" in line:
                return line.split("=")[1].strip()
