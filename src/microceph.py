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

import functools
import json
import logging
import subprocess
from socket import gethostname

import requests
import tenacity
from charms.operator_libs_linux.v2 import snap

import ceph
import utils
from microceph_client import Client, UnrecognizedClusterConfigOption

logger = logging.getLogger(__name__)

MAJOR_VERSIONS = {
    "17": "quincy",
    "18": "reef",
    "19": "squid",
}


def _az_flag_supported() -> bool:
    """Return True if the installed microceph supports --availability-zone."""
    try:
        output = utils.run_cmd(cmd=["microceph", "cluster", "bootstrap", "--help"], timeout=10)
        return "--availability-zone" in output
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def is_ready() -> bool:
    """Check if microceph snap is installed and bootstrapped/joined."""
    if not snap.SnapCache()["microceph"].present:
        logger.warning("Snap microceph not installed yet.")
        return False

    if not is_cluster_member(gethostname()):
        logger.warning("Microceph not bootstrapped yet.")
        return False

    if not ceph.cluster_has_quorum():
        logger.debug("Ceph cluster not in quorum, not ready yet")
        return False

    logger.debug("microceph cluster is bootstrapped and ready")
    return True


def cos_agent_refresh_cb(event):
    """Callback for cos-agent relation change."""
    logger.info("Entered CEPH COS AGENT REFRESH")

    if not is_ready():
        logger.debug("not bootstrapped, defer _on_refresh: %s", event)
        event.defer()
        return

    logger.debug("refreshing cos_agent relation")
    ceph.enable_ceph_monitoring()


def cos_agent_departed_cb(event):
    """Callback for cos-agent relation departed event."""
    logger.info("Entered CEPH COS AGENT DEPARTED")

    if not is_ready():
        logger.debug("not bootstrapped, skipping relation_deparated: %s", event)
        return

    logger.debug("disabling cos_agent relation.")
    ceph.disable_ceph_monitoring()


def remove_cluster_member(name: str, is_force: bool) -> None:
    """Remove a cluster member."""
    cmd = ["microceph", "cluster", "remove", name]
    if is_force:
        cmd.append("--force")
    utils.run_cmd(cmd)


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
        output = utils.run_cmd(cmd)
        return hostname in str(output)
    except subprocess.CalledProcessError as e:
        error_not_initialised = "Daemon not yet initialized"
        error_db_not_initialised = "Database is not yet initialized"
        error_db_waiting_upgrade = "Database is waiting for an upgrade"
        if error_not_initialised in e.stderr or error_db_not_initialised in e.stderr:
            # not a cluster member if daemon not initialised.
            return False
        elif error_db_waiting_upgrade in e.stderr:
            # host is a member of cluster being upgraded.
            return True
        else:
            raise e


def is_rgw_enabled(hostname: str) -> bool:
    """Check if RGW service is enabled on host.

    Raises ClusterServiceUnavailableException if cluster is not available.
    """
    client = Client.from_socket()
    services = client.cluster.list_services() or []
    for service in services:
        if service["service"] == "rgw" and service["location"] == hostname:
            return True

    return False


def list_cluster_configs():
    """List all cluster configs.

    Raises ClusterServiceUnavailableException
    """
    client = Client.from_socket()
    configs = client.cluster.get_config() or []
    return {config.get("key"): config.get("value") for config in configs}


def update_cluster_configs(configs: dict):
    """Update cluster configs.

    Raises ClusterServiceUnavailableException, UnrecognizedClusterConfigOption
    """
    client = Client.from_socket()
    configs_from_db = list_cluster_configs()

    def set_config(key, value, skip_restart):
        if key in configs_from_db and value == configs_from_db.get(key):
            return
        try:
            logger.debug(f"Setting microceph cluster config {key}")
            client.cluster.update_config(key, value, skip_restart)
        except UnrecognizedClusterConfigOption:
            raise UnrecognizedClusterConfigOption(f"Option {key} not recognized by microceph")

    # Only trigger restart on the last *changed* item to avoid multiple restarts.
    changed = [
        (k, v)
        for k, v in sorted(configs.items())
        if not (k in configs_from_db and v == configs_from_db.get(k))
    ]
    for key, value in changed[:-1]:
        set_config(key, value, skip_restart=True)
    if changed:
        set_config(changed[-1][0], changed[-1][1], skip_restart=False)


def delete_cluster_configs(configs: list):
    """Delete cluster configs.

    Raises ClusterServiceUnavailableException
    """
    client = Client.from_socket()
    configs_from_db = list_cluster_configs()
    configs_to_delete = set(configs) & set(configs_from_db.keys())
    for key in configs_to_delete:
        try:
            logger.debug(f"Removing microceph cluster config {key}")
            client.cluster.delete_config(key)
        except UnrecognizedClusterConfigOption:
            # If the key is not recognised by ceph/microceph just ignore.
            logger.warning(f"Option {key} not recognized by microceph")


def bootstrap_cluster(
    micro_ip: str = None,
    public_net: str = None,
    cluster_net: str = None,
    availability_zone: str = "",
) -> dict:
    """Bootstrap MicroCeph cluster."""
    cmd = ["microceph", "cluster", "bootstrap"]
    applied = {}

    if public_net:
        cmd.extend(["--public-network", public_net])
        applied["public_net"] = public_net

    if cluster_net:
        cmd.extend(["--cluster-network", cluster_net])
        applied["cluster_net"] = cluster_net

    if micro_ip:
        cmd.extend(["--microceph-ip", micro_ip])
        applied["microceph_ip"] = micro_ip

    if availability_zone:
        if _az_flag_supported():
            logger.debug(f"Using availability zone {availability_zone} for microceph cluster")
            cmd.extend(["--availability-zone", availability_zone])
            applied["availability_zone"] = availability_zone
        else:
            logger.warning("Ignoring --availability-zone: installed microceph does not support it")

    utils.run_cmd(cmd=cmd)
    return applied



def adopt_ceph_cluster(
    fsid: str = "",
    mon_hosts: list = [],
    admin_key: str = "",
    micro_ip: str = "",
    public_net: str = "",
    cluster_net: str = "",
    availability_zone: str = "",
) -> dict:
    """Bootstrap Microceph by adopting an existing Ceph cluster."""
    if not fsid or not mon_hosts or not admin_key:
        raise ValueError("fsid, mon_hosts and admin_key are required to adopt a cluster")

    logger.info(
        f"Got fsid: {fsid}, mon_hosts: {mon_hosts} and is_admin_key_provided: {admin_key is not None}"
    )

    cmd = [
        "microceph",
        "cluster",
        "adopt",
        "-",  # "-" signifies that admin key will be provided via stdin
        "--fsid",
        fsid,
        "--mon-hosts",
        ",".join(mon_hosts),
    ]
    applied = {"fsid": fsid, "mon_hosts": mon_hosts}

    if public_net:
        logger.debug(f"Using public network {public_net} for cluster")
        cmd.extend(["--public-network", public_net])
        applied["public_net"] = public_net

    if cluster_net:
        logger.debug(f"Using cluster network {cluster_net} for cluster")
        cmd.extend(["--cluster-network", cluster_net])
        applied["cluster_net"] = cluster_net

    if micro_ip:
        logger.debug(f"Using ip {micro_ip} for microceph cluster")
        cmd.extend(["--microceph-ip", micro_ip])
        applied["microceph_ip"] = micro_ip

    if availability_zone:
        if _az_flag_supported():
            logger.debug(f"Using availability zone {availability_zone} for microceph cluster")
            cmd.extend(["--availability-zone", availability_zone])
            applied["availability_zone"] = availability_zone
        else:
            logger.warning("Ignoring --availability-zone: installed microceph does not support it")

    utils.run_cmd_with_input(cmd=cmd, input_data=admin_key)
    return applied


def join_cluster(token: str, micro_ip: str = "", availability_zone: str = "", **kwargs):
    """Join node to MicroCeph cluster."""
    hostname = gethostname()
    if is_cluster_member(hostname):
        logger.debug("microceph unit host %s already a microcluster member", hostname)
        return

    cmd = ["microceph", "cluster", "join", token]

    if micro_ip:
        cmd.extend(["--microceph-ip", micro_ip])

    if availability_zone and _az_flag_supported():
        logger.debug(f"Using availability zone {availability_zone} for microceph cluster")
        cmd.extend(["--availability-zone", availability_zone])
    elif availability_zone:
        logger.warning("Ignoring --availability-zone: installed microceph does not support it")

    utils.run_cmd(cmd=cmd)


def enable_nfs(target: str, cluster_id: str, bind_addr: str) -> None:
    """Enable the NFS service on the target host with the given Cluster ID."""
    cmd = [
        "microceph",
        "enable",
        "nfs",
        "--target",
        target,
        "--cluster-id",
        cluster_id,
        "--bind-address",
        bind_addr,
    ]
    utils.run_cmd(cmd)


def disable_nfs(target: str, cluster_id: str) -> None:
    """Disable the NFS service on the target host with the given Cluster ID."""
    cmd = [
        "microceph",
        "disable",
        "nfs",
        "--target",
        target,
        "--cluster-id",
        cluster_id,
    ]
    utils.run_cmd(cmd)


def enable_rgw() -> None:
    """Enable RGW service."""
    cmd = ["microceph", "enable", "rgw"]
    utils.run_cmd(cmd)


def disable_rgw() -> None:
    """Disable RGW service."""
    cmd = ["microceph", "disable", "rgw"]
    utils.run_cmd(cmd)


def microceph_has_service(service_name) -> bool:
    """Returns whether the microceph snap has a service or not."""
    cmd = ["snap", "services", "microceph"]
    output = utils.run_cmd(cmd)

    return f"microceph.{service_name}" in output


# Disk CMDs and Helpers
def add_osd_cmd(
    spec: str, wal_dev: str = None, db_dev: str = None, wipe: bool = False, encrypt: bool = False
) -> None:
    """Executes MicroCeph add osd cmd with provided spec."""
    cmd = ["microceph", "disk", "add", spec]
    if wal_dev:
        cmd.extend(["--wal-device", wal_dev, "--wal-wipe"])
    if db_dev:
        cmd.extend(["--db-device", db_dev, "--db-wipe"])
    if wipe:
        cmd.append("--wipe")
    if encrypt:
        logger.debug("Called with --encrypt flag")
        _setup_dm_crypt()
        cmd.append("--encrypt")

    utils.run_cmd(cmd, timeout=900)


def _setup_dm_crypt() -> None:
    """Ensure dm-crypt is available and the snap plug is connected."""
    logger.debug("Setting up dm-crypt for encryption")

    @tenacity.retry(
        wait=tenacity.wait_fixed(5), stop=tenacity.stop_after_attempt(12), reraise=True
    )
    def _wait_for_microceph_daemon():
        """Wait for the microceph daemon to be ready after restart."""
        utils.run_cmd(["microceph", "disk", "list"], timeout=30)
        utils.run_cmd(
            ["snap", "run", "--shell", "microceph.daemon", "-c", "cryptsetup --version"],
            timeout=30,
        )

    # Try to load the module (noop if already loaded), required in case dm-crypt present
    # but not enabled
    try:
        utils.run_cmd(["modprobe", "dm_crypt"])
    except subprocess.CalledProcessError as e:
        logger.error(f"Encryption requested but dm-crypt is not available on this system: {e}")
        raise

    if not utils.snap_has_connection("microceph.daemon", "dm-crypt"):
        logger.info(
            "dm-crypt was not connected to microceph, connecting and restarting microceph."
        )
        utils.run_cmd(["snap", "connect", "microceph:dm-crypt"])
        utils.run_cmd(["snap", "restart", "microceph.daemon"])
        _wait_for_microceph_daemon()


def add_batch_osds(disks: list) -> None:
    """Enroll multiple disks as OSD."""
    cmd = ["microceph", "disk", "add"]

    if not disks:
        # nothing to do.
        return

    # The disk add command takes a space separated list
    # of block devices as params.
    cmd.extend(disks)
    utils.run_cmd(cmd)


def _append_optional_match_args(cmd: list, *flag_value_pairs: tuple[str, str | None]) -> None:
    """Append optional `--flag value` pairs for non-empty values."""
    for flag, value in flag_value_pairs:
        if value:
            cmd.extend([flag, value])


def _append_enabled_flags(cmd: list, *flag_pairs: tuple[bool, str]) -> None:
    """Append enabled boolean flags in order."""
    for enabled, flag in flag_pairs:
        if enabled:
            cmd.append(flag)


def add_disk_match_cmd(
    osd_match: str,
    *,
    wal_match: str = None,
    wal_size: str = None,
    db_match: str = None,
    db_size: str = None,
    wipe: bool = False,
    encrypt: bool = False,
    wal_wipe: bool = False,
    wal_encrypt: bool = False,
    db_wipe: bool = False,
    db_encrypt: bool = False,
    dry_run: bool = False,
) -> str:
    """Execute MicroCeph disk add with DSL-based OSD/WAL/DB matching."""
    logger.debug(
        "Preparing microceph disk add command osd_match=%s wal_match=%s wal_size=%s "
        "db_match=%s db_size=%s wipe=%s encrypt=%s wal_wipe=%s wal_encrypt=%s "
        "db_wipe=%s db_encrypt=%s dry_run=%s",
        osd_match,
        wal_match,
        wal_size,
        db_match,
        db_size,
        wipe,
        encrypt,
        wal_wipe,
        wal_encrypt,
        db_wipe,
        db_encrypt,
        dry_run,
    )
    cmd = ["microceph", "disk", "add", "--osd-match", osd_match]
    wal_enabled = bool(wal_match)
    db_enabled = bool(db_match)

    if wal_enabled:
        _append_optional_match_args(
            cmd,
            ("--wal-match", wal_match),
            ("--wal-size", wal_size),
        )
        _append_enabled_flags(cmd, (wal_wipe, "--wal-wipe"), (wal_encrypt, "--wal-encrypt"))

    if db_enabled:
        _append_optional_match_args(
            cmd,
            ("--db-match", db_match),
            ("--db-size", db_size),
        )
        _append_enabled_flags(cmd, (db_wipe, "--db-wipe"), (db_encrypt, "--db-encrypt"))
    _append_enabled_flags(cmd, (wipe, "--wipe"), (encrypt, "--encrypt"), (dry_run, "--dry-run"))

    logger.debug(
        "Built microceph disk add command wal_enabled=%s db_enabled=%s cmd=%s",
        wal_enabled,
        db_enabled,
        cmd,
    )

    if encrypt or (wal_enabled and wal_encrypt) or (db_enabled and db_encrypt):
        logger.debug(
            "Encryption requested for disk add command; ensuring dm-crypt is available "
            "osd_encrypt=%s wal_encrypt=%s db_encrypt=%s",
            encrypt,
            wal_encrypt,
            db_encrypt,
        )
        _setup_dm_crypt()

    return utils.run_cmd(cmd, timeout=900)


def get_snap_info(snap_name):
    """Get snap info from the charm store."""
    url = f"https://api.snapcraft.io/v2/snaps/info/{snap_name}"
    headers = {"Snap-Device-Series": "16"}  # magic header val for snapstore
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def list_disk_cmd(host_only: bool = False) -> dict:
    """Fetches MicroCeph configured and unpartitioned disks as a dict."""
    cmd = ["microceph", "disk", "list", "--json"]
    if host_only:
        cmd.append("--host-only")
    return json.loads(utils.run_cmd(cmd))


def remove_disk_cmd(osd_num: int, force: bool = False) -> None:
    """Removes requested OSD."""
    cmd = ["microceph", "disk", "remove", str(osd_num)]
    if force:
        cmd.append("--bypass-safety-checks")
        # NOTE(utkarshbhatthere): Force removal of OSD is performed when the
        # action causes osd count to fall below three across unique hosts.
        # The flag below prevents automatic scaledown of failure domain to OSD
        # as it makes the storage cluster unsafe against host failures.
        cmd.append("--prohibit-crush-scaledown")
    else:
        # In Microceph, operators need to specify this flag manually. On Juju,
        # and thus, the charms, we need a more hands-off approach, and so
        # failure domains changes are enabled by default.
        cmd.append("--confirm-failure-domain-downgrade")
    utils.run_cmd(cmd)


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
        disk_info = json.loads(utils.run_cmd(["lsblk", disk, "--json"]))["blockdevices"]
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


def set_pool_size(pools: str, size: int):
    """Set the size for one or more pools."""
    pools_list = pools.split(",")
    cmd = ["sudo", "microceph", "pool", "set-rf", "--size", str(size)]
    cmd.extend(pools_list)
    utils.run_cmd(cmd)


def export_cluster_token(remote_name: str) -> str:
    """Generate cluster token for remote cluster."""
    return utils.run_cmd(["microceph", "cluster", "export", remote_name])


def import_remote_token(local_name, remote_name, remote_token):
    """Import a remote microceph cluster using token."""
    return utils.run_cmd(
        [
            "microceph",
            "remote",
            "import",
            remote_name,
            remote_token,
            "--local-name",
            local_name,
        ]
    )


def remove_remote_cluster(remote_name: str):
    """Remove a remote microceph cluster record."""
    return utils.run_cmd(["microceph", "remote", "remove", remote_name])
