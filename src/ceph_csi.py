#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
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

"""Handle Charm's Ceph-CSI relation events."""

import json
import logging
from typing import Callable, Optional, Set

from ops.charm import RelationEvent
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import ActiveStatus, BlockedStatus
import ops_sunbeam.compound_status as compound_status
from ops_sunbeam.charm import OSBaseOperatorCharm
from ops_sunbeam.relation_handlers import RelationHandler

import ceph
import utils

logger = logging.getLogger(__name__)


class CephCSIConnectedEvent(RelationEvent):
    """ceph-csi connected event."""

    pass


class CephCSIDepartedEvent(RelationEvent):
    """ceph-csi relation has departed event."""

    pass


class CephCSIReconcileEvent(RelationEvent):
    """ceph-csi relation reconciliation event."""

    pass


class CephCSIEvents(ObjectEvents):
    """Events class for `on`."""

    ceph_csi_connected = EventSource(CephCSIConnectedEvent)
    ceph_csi_departed = EventSource(CephCSIDepartedEvent)
    ceph_csi_reconcile = EventSource(CephCSIReconcileEvent)


class CephCSIProvides(Object):
    """Interface for ceph-csi provider."""

    on = CephCSIEvents()  # type: ignore[assignment]

    def __init__(self, charm, relation_name="ceph-csi"):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        # React to ceph-csi relations.
        self.framework.observe(charm.on[relation_name].relation_joined, self._on_relation_changed)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)
        self.framework.observe(
            charm.on[relation_name].relation_departed, self._on_relation_departed
        )

        # React to ceph peers relation changes.
        self.framework.observe(charm.on["peers"].relation_changed, self._on_ceph_peers)
        self.framework.observe(charm.on["peers"].relation_departed, self._on_ceph_peers)

    def _on_relation_changed(self, event: RelationEvent) -> None:
        if not self.model.unit.is_leader():
            return

        logger.info("ceph-csi relation changed")

        if not self.charm.ready_for_service():
            logger.info("Not processing request as service is not yet ready")
            event.defer()
            return

        if ceph.get_osd_count() == 0:
            logger.info("Storage not available, deferring event.")
            event.defer()
            return

        self.on.ceph_csi_connected.emit(event.relation)

    def _on_relation_departed(self, event: RelationEvent) -> None:
        if not self.model.unit.is_leader():
            return

        if event.relation.app is None:
            logger.debug("ceph-csi relation departed with no remote application; skipping.")
            return
        logger.info("ceph-csi relation departed")
        self.on.ceph_csi_departed.emit(event.relation)

    def _on_ceph_peers(self, event: RelationEvent) -> None:
        if not self.model.unit.is_leader():
            return

        if ceph.get_osd_count() == 0:
            logger.info("Storage not available, deferring event.")
            event.defer()
            return

        if not self.model.relations.get(self.relation_name):
            logger.debug("No ceph-csi relations to reconcile.")
            return

        logger.info("ceph-csi peers changed, reconciling relations")
        self.on.ceph_csi_reconcile.emit(event.relation)


class CephCSIProvidesHandler(RelationHandler):
    """Handler for the ceph-csi relation."""

    def __init__(
        self,
        charm: OSBaseOperatorCharm,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self) -> Object:
        logger.debug("Setting up ceph-csi event handler")

        ceph_csi = CephCSIProvides(self.charm, self.relation_name)
        self.framework.observe(ceph_csi.on.ceph_csi_connected, self._on_ceph_csi_connected)
        self.framework.observe(ceph_csi.on.ceph_csi_reconcile, self._on_ceph_csi_reconcile)
        self.framework.observe(ceph_csi.on.ceph_csi_departed, self._on_ceph_csi_departed)
        return ceph_csi

    @property
    def ready(self) -> bool:
        return True

    def set_status(self, status: compound_status.Status) -> None:
        status.set(ActiveStatus(""))

    def _parse_workloads(self, value: Optional[str]) -> Set[str]:
        if not value:
            return set()

        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                items = parsed
            elif isinstance(parsed, str):
                items = [parsed]
            else:
                items = []
        except json.JSONDecodeError:
            items = [v.strip() for v in value.replace(";", ",").split(",")]

        return {str(v).strip().lower() for v in items if str(v).strip()}

    def _get_workloads(self, relation) -> Set[str]:
        remote_data = relation.data[relation.app]
        workloads = remote_data.get("workloads")
        parsed = self._parse_workloads(workloads)
        return parsed or {"rbd"}

    def _ensure_fs_volume(self, fs_name: str) -> None:
        for volume in ceph.list_fs_volumes():
            if volume.get("name") == fs_name:
                self._configure_cephfs_mds(fs_name)
                return
        ceph.create_fs_volume(fs_name)
        self._configure_cephfs_mds(fs_name)

    def _configure_cephfs_mds(self, fs_name: str) -> None:
        max_mds = self._config_int("active-mds-per-volume", 1)
        standby_count = self._config_int("standby-mds-per-volume", 0)
        utils.run_cmd(["microceph.ceph", "fs", "set", fs_name, "max_mds", str(max_mds)])
        utils.run_cmd(
            [
                "microceph.ceph",
                "fs",
                "set",
                fs_name,
                "standby_count_wanted",
                str(standby_count),
            ]
        )

    def _mds_daemon_count(self) -> int:
        try:
            data = json.loads(
                utils.run_cmd(["microceph.ceph", "mds", "stat", "--format", "json"])
            )
        except Exception as exc:
            logger.warning("Failed to fetch mds stats: %s", exc)
            return 0

        fsmap = data.get("fsmap", data)
        standbys = fsmap.get("standbys", [])
        filesystems = fsmap.get("filesystems", [])

        active = 0
        for fs in filesystems:
            mdsmap = fs.get("mdsmap", {})
            up = mdsmap.get("up", {})
            if isinstance(up, dict):
                active += len(up)
            elif isinstance(up, list):
                active += len(up)

        return active + len(standbys)

    def _has_sufficient_mds(self) -> bool:
        active = self._config_int("active-mds-per-volume", 1)
        standby = self._config_int("standby-mds-per-volume", 0)
        required = active + standby
        if required <= 0:
            return True

        available = self._mds_daemon_count()
        if available < required:
            logger.error(
                "Insufficient MDS daemons: required=%d available=%d", required, available
            )
            return False
        return True

    def _config_int(self, key: str, default: int) -> int:
        value = self.model.config.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _get_pool_name_map(self) -> dict:
        cmd = ["microceph.ceph", "osd", "pool", "ls", "detail", "--format", "json"]
        pool_list = json.loads(utils.run_cmd(cmd))
        return {pool["pool_id"]: pool["pool_name"] for pool in pool_list}

    def _get_cephfs_pools(self, fs_name: str) -> Set[str]:
        cmd = ["microceph.ceph", "fs", "get", fs_name, "--format", "json"]
        fs_info = json.loads(utils.run_cmd(cmd))

        mdsmap = fs_info.get("mdsmap", {})
        data_pools = mdsmap.get("data_pools")
        metadata_pool = mdsmap.get("metadata_pool")

        pool_name_map = self._get_pool_name_map()
        pools = set()

        if isinstance(data_pools, list):
            for pool_id in data_pools:
                pools.add(pool_name_map.get(pool_id))
        if metadata_pool is not None:
            pools.add(pool_name_map.get(metadata_pool, str(metadata_pool)))

        if pools:
            return pools

        logger.warning("Unable to deduce CephFS pools for %s; using defaults", fs_name)
        return {f"{fs_name}.data", f"{fs_name}.meta"}

    def _ensure_rbd_pool(self, pool_name: str) -> None:
        pool = ceph.ReplicatedPool(
            service="admin",
            name=pool_name,
            replicas=self.model.config.get("default-pool-size"),
            app_name="rbd",
        )
        pool.create()

    def _format_client_name(self, user_id: str) -> str:
        if user_id.startswith("client."):
            return user_id
        return f"client.{user_id}"

    def _service_relation(self, relation) -> bool:
        if not self.model.unit.is_leader():
            return False

        workloads = self._get_workloads(relation)
        if not workloads:
            logger.info("No workloads provided for ceph-csi relation")
            return False

        rbd_pool = f"rbd.{relation.app.name}"
        cephfs_name = relation.app.name
        client_name = self._format_client_name(f"csi-{relation.app.name}")

        pool_list = []
        if "rbd" in workloads:
            self._ensure_rbd_pool(rbd_pool)
            pool_list.append(rbd_pool)

        cephfs_pools = set()
        if "cephfs" in workloads:
            if not self._has_sufficient_mds():
                self.status.set(
                    BlockedStatus("Insufficient MDS daemons for cephfs workload")
                )
                return False
            self._ensure_fs_volume(cephfs_name)
            cephfs_pools = self._get_cephfs_pools(cephfs_name)
            pool_list.extend(sorted(cephfs_pools))

        caps = {
            "mon": [
                "allow r",
                'allow command "osd blacklist"',
                'allow command "osd blocklist"',
            ],
            "osd": ["allow rwx"],
        }
        if "cephfs" in workloads:
            caps.update({"mds": ["allow rw"], "mgr": ["allow rw"]})

        key = ceph.get_named_key(client_name, caps=caps, pool_list=pool_list)

        relation_data = {
            "fsid": utils.get_fsid(),
            "mon_hosts": json.dumps(utils.get_mon_addresses()),
            "user_id": client_name,
            "user_key": key,
        }
        if "rbd" in workloads:
            relation_data["rbd_pool"] = rbd_pool
        if "cephfs" in workloads:
            relation_data["cephfs_fs_name"] = cephfs_name

        relation.data[self.model.app].update(relation_data)
        return True

    def _on_ceph_csi_connected(self, event: RelationEvent) -> None:
        if not self._service_relation(event.relation):
            logger.error("Failed to service ceph-csi relation, deferring")
            event.defer()
            return
        self.status.set(ActiveStatus(""))

    def _on_ceph_csi_reconcile(self, event: RelationEvent) -> None:
        for relation in self.model.relations.get(self.relation_name, []):
            if not self._service_relation(relation):
                logger.error("Failed to reconcile ceph-csi relation")
                event.defer()
                return
        self.status.set(ActiveStatus(""))

    def _on_ceph_csi_departed(self, event: RelationEvent) -> None:
        client_name = self._format_client_name(f"csi-{event.relation.app.name}")
        try:
            ceph.remove_named_key(client_name)
        except Exception as exc:
            logger.warning("Failed removing cephx key %s: %s", client_name, exc)

        self.status.set(ActiveStatus(""))
