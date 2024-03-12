#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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

"""Handle Ceph commands."""

import enum
import json
import logging
import subprocess
from typing import Tuple

import requests

logger = logging.getLogger(__name__)

MAJOR_VERSIONS = {
    "17": "quincy",
    "18": "reef",
    "19": "squid",
}


def _run_cmd(cmd: list) -> str:
    """Execute provided command via subprocess."""
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)
        logger.debug(f"Command {' '.join(cmd)} finished; Output: {process.stdout}")
        return process.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed executing cmd: {cmd}, error: {e.stderr}")
        raise e


def remove_cluster_member(name: str, is_force: bool) -> None:
    """Remove a cluster member."""
    cmd = ["microceph", "cluster", "remove", name]
    if is_force:
        cmd.append("--force")
    _run_cmd(cmd)


def get_mon_public_addresses() -> list:
    """Returns first mon host address as read from the ceph.conf file."""
    conf_file_path = "/var/snap/microceph/current/conf/ceph.conf"
    public_addrs = []
    with open(conf_file_path, "r") as conf_file:
        lines = conf_file.readlines()
        for line in lines:
            if "mon host" in line:
                addrs = line.strip().split(" ")[-1].split(",")
                logger.debug(f"Found public addresses {addrs} in conf file.")
                public_addrs.extend(addrs)
                break

    return public_addrs


def is_cluster_member(hostname: str) -> bool:
    """Checks if the provided host is part of the microcluster."""
    cmd = ["microceph", "status"]
    try:
        output = _run_cmd(cmd)
        return hostname in str(output)
    except subprocess.CalledProcessError as e:
        error_not_initialised = "Daemon not yet initialized"
        if error_not_initialised in e.stderr:
            # not a cluster member if daemon not initialised.
            return False
        else:
            raise e


# Disk CMDs and Helpers
def add_osd_cmd(spec: str, wal_dev: str = None, db_dev: str = None) -> None:
    """Executes MicroCeph add osd cmd with provided spec."""
    cmd = ["microceph", "disk", "add", spec]
    if wal_dev:
        cmd.extend(["--wal-device", wal_dev, "--wal-wipe"])
    if db_dev:
        cmd.extend(["--db-device", db_dev, "--db-wipe"])
    _run_cmd(cmd)


def add_batch_osds(disks: list) -> None:
    """Enroll multiple disks as OSD."""
    cmd = ["microceph", "disk", "add"]

    if not disks:
        # nothing to do.
        return

    # The disk add command takes a space separated list
    # of block devices as params.
    cmd.extend(disks)
    _run_cmd(cmd)


def get_snap_info(snap_name):
    """Get snap info from the charm store."""
    url = f"https://api.snapcraft.io/v2/snaps/info/{snap_name}"
    headers = {"Snap-Device-Series": "16"}  # magic header val for snapstore
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def list_disk_cmd() -> dict:
    """Fetches MicroCeph configured and unpartitioned disks as a dict."""
    cmd = ["microceph", "disk", "list", "--json"]
    return json.loads(_run_cmd(cmd))


def remove_disk_cmd(osd_num: int, force: bool = False) -> None:
    """Removes requested OSD."""
    cmd = ["microceph", "disk", "remove", str(osd_num)]
    if force:
        cmd.append("--bypass-safety-checks")
    _run_cmd(cmd)


def enroll_disks_as_osds(disks: list) -> None:
    """Enrolls the provided block devices as OSDs."""
    if not disks:
        return

    available_disks = []
    for disk in disks:
        if not _is_block_device_enrollable(disk):
            err_str = f"provided disk {disk} is not enrollable as an OSD."
            logger.error(err_str)
            raise ValueError(err_str)

        available_disks.append(disk)

    # pass disks as space separated arguments.
    add_batch_osds(available_disks)


def _get_disk_info(disk: str) -> dict:
    """Fetches disk info from lsblk as a python dict."""
    try:
        disk_info = json.loads(_run_cmd(["lsblk", disk, "--json"]))["blockdevices"]
        return disk_info[0]
    except subprocess.CalledProcessError as e:
        if "not a block device" in e.stderr:
            return {}
        else:
            raise e


def _is_block_device_enrollable(disk: str) -> bool:
    """Checks if the provided block device is enrollable as an OSD."""
    device = _get_disk_info(disk)

    if not device:
        return False

    # the json interpretation of [null] -> [None].
    if device["mountpoints"] != [None]:
        logger.warning(f"Disk {disk} has mounts.")
        return False

    if "children" in device.keys():
        logger.warning(f"Disk {disk} has partitions.")
        return False

    return True


def get_snap_tracks(snap_name):
    """Get snap tracks from the charm store."""
    info = get_snap_info(snap_name)
    tracks = {item["channel"]["track"] for item in info["channel-map"]}
    return tracks


def can_upgrade_snap(current, new: str) -> bool:
    """Check if we can upgrade snap to the provided track."""
    if not new:
        return False
    if new == "latest":  # upgrade to latest is always allowed
        return True

    # track must exist
    if new.lower() not in get_snap_tracks("microceph"):
        logger.warning(f"Track {new} does not exist for snap microceph")
        return False

    # resolve major version if set to latest currently
    if current == "latest":
        ver = get_snap_info("microceph")["latest"]
        current = MAJOR_VERSIONS[ver]
        logger.debug(f"Resolved 'latest' track to {current}")

    # We must not downgrade the major version of the snap
    alphabet = [chr(i) for i in range(ord("a"), ord("z") + 1)]
    start_index = alphabet.index("q")  # quincy is our first
    # q, r, ... z, a, b, ... p
    succession = alphabet[start_index:] + alphabet[:start_index]
    newer = succession.index(current[0]) <= succession.index(new[0])
    return newer


class CephHealth(enum.Enum):
    """Enumerate ceph health status."""

    Ok = "HEALTH_OK"
    Warn = "HEALTH_WARN"
    Err = "HEALTH_ERR"
    Unknown = "HEALTH_UNKNOWN"

    @classmethod
    def from_string(cls, health_str: str):
        """Construct a CephHealth object from a string."""
        for health in cls:
            if health.value == health_str:
                return health
        return cls.Unknown

    def __str__(self):
        """Return the string representation of the health."""
        return self.value


class CephStatus(object):
    """Class to handle ceph health checks."""

    def ceph_health(self) -> Tuple[CephHealth, str]:
        """Return the health of the monitor."""
        cmd = ["sudo", "microceph.ceph", "health", "detail", "--format=json"]
        try:
            output = _run_cmd(cmd)
        except subprocess.CalledProcessError:
            # ceph health detail command failed, possibly mon wasn't reachable
            # as it's restarting. Return unknown health for this case.
            return CephHealth.Unknown, "fault running ceph health detail command"
        res = json.loads(output.strip())
        return CephHealth.from_string(res["status"]), res["checks"]
