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


"""The cluster module manages cluster-wide operations."""

import json
import logging
import subprocess
import uuid
from socket import gethostname
from typing import Tuple

import ops.charm
import ops_sunbeam.guard as sunbeam_guard
import tenacity
from charms.operator_libs_linux.v2 import snap

import charm
import microceph
import relation_handlers
import utils
from ceph import CephHealth, CephStatus

logger = logging.getLogger(__name__)
UPGRADE_HEALTH_BLOCKED_MSG_PREFIX = "Cannot upgrade, ceph health not ok"
UPGRADE_SNAP_QUERY_BLOCKED_MSG_PREFIX = "Cannot upgrade, unable to query snap store via snapd"


class ClusterNodes(ops.framework.Object):
    """ClusterNodes manages adding and joining nodes to the microceph cluster."""

    def __init__(self, charm: "charm.MicroCephCharm"):
        super().__init__(charm, "cluster-nodes")
        self.charm = charm

    def add_node_to_cluster(self, event: ops.framework.EventBase) -> None:
        """Add node to microceph cluster."""
        logging.debug(f"Adding node to cluster: {event}")
        if not event.unit:
            logger.error("Add node triggered without unit information.")
            return
        # get hostname using unit name.
        hostnames = self.charm.peers.get_all_unit_values(
            key=event.unit.name, include_local_unit=True
        )
        logging.debug(f"Hostnames for {event}: {hostnames}")
        if not hostnames:
            logging.info(f"No hostname found for: {event}")
            return

        cmd = ["microceph", "cluster", "add", hostnames[0]]
        try:
            out = utils.run_cmd(cmd)
            token = out.strip()
            self.charm.peers.set_app_data({f"{event.unit.name}.join_token": token})
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(e.stderr)
            # MicroCeph renamed the table from internal_token_records to
            # core_token_records in the squid release. Handle both.
            if "UNIQUE constraint failed" not in e.stderr or "token_records.name" not in e.stderr:
                raise e

    def join_node_to_cluster(self, event: ops.framework.EventBase) -> None:
        """Join node to microceph cluster."""
        if not event.unit:
            logger.warning("Add node triggered for without unit information.")
            return

        if self.charm.peers.interface.state.joined is True:
            logger.info("Unit has already joined the cluster")
            return

        logger.debug(f"Adding {event.unit.name} to cluster.")
        token = self.charm.peers.get_app_data(f"{event.unit.name}.join_token")
        if not token:
            raise sunbeam_guard.BlockedExceptionError(f"join token not found for {gethostname()}")

        params = self.charm._get_bootstrap_params()
        # Only pass --availability-zone if the cluster was bootstrapped with
        # AZ support.  Mixing AZ and non-AZ nodes is not allowed, so if the
        # flag is absent (older cluster or bootstrap without AZ) we suppress it.
        if not self.charm.peers.get_app_data("cluster_uses_az"):
            params["availability_zone"] = ""

        try:
            microceph.join_cluster(token=token, **params)
            self.charm.peers.interface.state.joined = True
            self.charm.peers.set_unit_data({"joined": json.dumps(True)})
            logger.debug("Joined cluster successfully.")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(e.stderr)
            raise e


class ClusterUpgrades(ops.framework.Object):
    """ClusterUpgrades manages snap upgrades across the cluster."""

    charm = None
    _stored = ops.framework.StoredState()

    def __init__(self, charm: "charm.MicroCephCharm"):
        super().__init__(charm, "cluster-upgrade")
        self.charm = charm

    @property
    def peer_int(self):
        """Get the peer interface."""
        return self.charm.peers.interface

    @property
    def channel(self) -> str:
        """Get the snap channel."""
        return self.charm.channel

    @channel.setter
    def channel(self, value: str) -> None:
        self.charm.channel = value

    @property
    def track(self) -> str:
        """Get the snap track."""
        c = self.channel
        if not self.channel:
            c = self.model.config["snap-channel"]
        return c.split("/")[0]

    def upgrade_requested(self, chan: str) -> bool:
        """Check if a snap upgrade was requested."""
        logger.debug(f"Requested, current channel: {chan}, {self.channel}")
        return self.channel != chan

    def can_upgrade_charm_payload(self, snap_chan: str) -> Tuple[bool, str]:
        """Check if snap can be upgraded."""
        try:
            allowed = microceph.can_upgrade_snap(self.track, snap_chan.split("/")[0])
        except (snap.SnapAPIError, OSError) as e:
            # The track lookup through snapd failed: snapd could not reach the
            # store or the store proxy, returned an API error, is down, or timed
            # out. Refuse for now instead of failing the hook; update-status
            # retries the check while the upgrade is pending.
            msg = f"{UPGRADE_SNAP_QUERY_BLOCKED_MSG_PREFIX}: {type(e).__name__}: {e}"
            logger.warning(msg)
            return False, msg
        if not allowed:
            msg = f"Cannot upgrade from {self.channel} to {snap_chan}"
            logger.warning(msg)
            return False, msg
        return self._check_upgrade_health()

    def _check_upgrade_health(self) -> Tuple[bool, str]:
        health, det = CephStatus().ceph_health()
        if health != CephHealth.Ok:
            msg = f"{UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: {health}, {det}"
            logger.warning(msg)
            return False, msg
        return True, ""

    def queue_refresh(self) -> dict:
        """Pin the current channel's published revision and queue a rolling refresh."""
        if not self.peer_int.peers_rel or not self.charm.ready_for_service():
            raise ValueError("MicroCeph is not ready for a refresh")
        if self.peer_int.get_upgrade_info() or self.upgrade_requested(
            self.model.config["snap-channel"]
        ):
            raise ValueError("A MicroCeph snap upgrade is already pending")

        channel = self.channel
        # snapd reports channel-map keys as track/risk, even for shorthand config values.
        if "/" not in channel:
            channel = (
                f"latest/{channel}"
                if channel in {"stable", "candidate", "beta", "edge"}
                else f"{channel}/stable"
            )
        info = microceph.get_snap_info("microceph")
        revision = str(info.get("channels", {}).get(channel, {}).get("revision", ""))
        if not revision.isdecimal():
            raise ValueError(f"No published MicroCeph snap revision found for {channel}")
        if str(snap.SnapCache()["microceph"].revision) == revision:
            return {"status": "up-to-date", "revision": revision}

        ok, msg = self._check_upgrade_health()
        if not ok:
            raise ValueError(msg)
        nodes = [self.model.unit.name, *sorted(u.name for u in self.peer_int.all_joined_units())]
        self.peer_int.set_upgrade_info(str(uuid.uuid4()), self.channel, nodes, revision)
        return {"status": "queued", "revision": revision}

    def process_local_refresh(self) -> bool:
        """Process a queued refresh if this unit is next, leaving failures retryable."""
        plan = self.peer_int.get_upgrade_info()
        if not plan.get("revision") or plan["nodes"][0] != self.model.unit.name:
            return False

        unit_data = self.peer_int.peers_rel.data[self.model.unit]
        if unit_data.get("upgrade-done") != plan["nonce"]:
            logger.info("Refreshing MicroCeph to revision %s", plan["revision"])
            self.perform_upgrade(plan["channel"], plan["revision"])
            logger.info("MicroCeph refresh to revision %s complete on this unit", plan["revision"])
        if self.model.unit.is_leader():
            self.peer_int.advance_upgrade(plan)
        else:
            self.peer_int.set_unit_data({"upgrade-done": plan["nonce"]})
        return True

    def perform_upgrade(self, channel: str, revision: str | None = None) -> None:
        """Perform the snap upgrade on this node."""
        node = self.model.unit.name
        logger.debug("Upgrading %s on %s (revision: %s)", node, channel, revision)
        if revision:
            ok, msg = self._check_upgrade_health()
            if not ok:
                raise sunbeam_guard.BlockedExceptionError(msg)

        # Check if any of the non-service commands are running
        # Upgrading while a non-service command is running fails; checking
        # this here so we can return a meaningful error message
        cmds_pat = r"/snap/microceph/.*/(microceph|ceph|rados|rbd)"
        try:
            # As some of these are Python scripts we need to check against the
            # full command line
            subprocess.run(["pgrep", "-f", cmds_pat], check=True)
            msg = "Cannot upgrade, one of microceph|ceph|rados|rbd commands is running"
            logger.warning(msg)
            raise sunbeam_guard.BlockedExceptionError(msg)
        except subprocess.CalledProcessError:
            # pgrep didn't find the command and returned non-zero
            logger.debug("check running programs: none running")

        # TODO(peter) possibly set noout, noin for cases where upgrades take longer

        self._refresh_snap(channel, revision)

        @tenacity.retry(
            wait=tenacity.wait_fixed(8),
            stop=tenacity.stop_after_delay(900),
            retry=tenacity.retry_if_result(lambda is_healthy: not is_healthy),
        )
        def poll_ok():
            """Checks Ceph health.

            Needs 3 'Ok' checks in a row before succeeding.
            """
            health, _ = CephStatus().ceph_health()

            # initialize
            if not hasattr(poll_ok, "consecutive_ok"):
                poll_ok.consecutive_ok = 0

            if health == CephHealth.Ok:
                poll_ok.consecutive_ok += 1
            else:
                poll_ok.consecutive_ok = 0

            logger.debug(f"Consecutive healthy checks: {poll_ok.consecutive_ok}")
            return poll_ok.consecutive_ok >= 3

        poll_ok()  # wait for ceph to be healthy

        health, det = CephStatus().ceph_health()  # check again, get details
        if health != CephHealth.Ok:
            msg = f"Upgrade on {node} to {channel} failed: {health}, {det}"
            logger.error(msg)
            # don't continue on a failed upgrade
            raise sunbeam_guard.BlockedExceptionError(msg)

        logger.debug(f"Upgrade on {node} to {channel} done")

    def _refresh_snap(self, channel: str, revision: str | None) -> None:
        mc_snap = snap.SnapCache()["microceph"]
        if not revision:
            mc_snap.ensure(snap.SnapState.Present, channel=channel)
        elif str(mc_snap.revision) != revision:
            # A targeted refresh works with the automatic refresh hold in place.
            utils.run_cmd(["snap", "refresh", "microceph", f"--revision={revision}"], timeout=900)

    def init_upgrade(self, snap_chan: str):
        """Kick off the snap upgrade."""
        logger.debug(f"Preparing upgrade from {self.channel} to {snap_chan}")
        self.channel = snap_chan

        # first upgrade this node. upgrade synchronously as we're still in
        # the config handler
        self.perform_upgrade(snap_chan)

        # then initialise peer upgrade upgrade by setting upgrade info on the
        # peer relation
        nonce = str(uuid.uuid4())
        peers = self.peer_int.all_joined_units()
        upgrade_nodes = sorted([u.name for u in peers])
        logger.debug(f"Upgrade init: {upgrade_nodes}, {snap_chan}, {nonce}")
        self.peer_int.set_upgrade_info(
            nonce,
            snap_chan,
            upgrade_nodes,
        )

    def upgrade_node_request(self, event: relation_handlers.UpgradeNodeRequestEvent):
        """Handle upgrade request for this node."""
        node = event.node
        channel = event.channel
        nonce = event.nonce
        logger.debug(f"Upgrading node {node}, {channel}, {nonce}")

        if node == self.model.unit.name:
            self.perform_upgrade(channel)  # raise exception on failure
            self.peer_int.on.upgrade_done.emit(node=node, channel=channel, nonce=nonce)

    def upgrade_node_done(self, event: relation_handlers.UpgradeNodeDoneEvent):
        """Signal upgrade done for this node."""
        logger.debug(f"Handle upgrade done {event.nonce}")
        self.peer_int.set_unit_data({"upgrade-done": event.nonce})
