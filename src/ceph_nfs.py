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


"""Handle Charm's NFS Client Events."""

import itertools
import json
import logging
from typing import Callable

import ops_sunbeam.compound_status as compound_status
from ops.charm import CharmBase, RelationEvent
from ops.framework import (
    EventBase,
    EventSource,
    Object,
    ObjectEvents,
)
from ops.model import ActiveStatus, BlockedStatus
from ops_sunbeam.relation_handlers import RelationHandler

import ceph
import microceph
import utils
from microceph_client import Client

logger = logging.getLogger(__name__)


class CephNfsConnectedEvent(RelationEvent):
    """ceph-nfs connected event."""

    pass


class CephNfsDepartedEvent(RelationEvent):
    """ceph-nfs relation has departed event."""

    pass


class CephNfsReconcileEvent(RelationEvent):
    """ceph-nfs relation reconciliation event."""

    pass


class CephNfsEvents(ObjectEvents):
    """Events class for `on`."""

    ceph_nfs_connected = EventSource(CephNfsConnectedEvent)
    ceph_nfs_departed = EventSource(CephNfsDepartedEvent)
    ceph_nfs_reconcile = EventSource(CephNfsReconcileEvent)


class CephNfsProvides(Object):
    """Interface for ceph-nfs-client provider."""

    on = CephNfsEvents()

    def __init__(self, charm, relation_name="ceph-nfs"):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        # React to ceph-nfs relations.
        self.framework.observe(charm.on[relation_name].relation_joined, self._on_relation_changed)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)
        self.framework.observe(
            charm.on[relation_name].relation_departed, self._on_relation_departed
        )

        # React to ceph peers relations.
        self.framework.observe(charm.on["peers"].relation_departed, self._on_ceph_peers)
        self.framework.observe(charm.on["peers"].relation_changed, self._on_ceph_peers)

    def _on_relation_changed(self, event):
        """Prepare relation for data from requiring side."""
        if not self.model.unit.is_leader():
            return

        logger.info("_on_relation_changed event")

        if not self.charm.ready_for_service():
            logger.info("Not processing request as service is not yet ready")
            event.defer()
            return

        if ceph.get_osd_count() == 0:
            logger.info("Storage not available, deferring event.")
            event.defer()
            return

        self.on.ceph_nfs_connected.emit(event.relation)

    def _on_relation_departed(self, event):
        """Cleanup relation after departure."""
        if not self.model.unit.is_leader() or event.relation.app == self.charm.app:
            return

        logger.info("_on_relation_departed event")
        self.on.ceph_nfs_departed.emit(event.relation)

    def _on_ceph_peers(self, event):
        """Handle ceph peers relation events."""
        if not self.model.unit.is_leader():
            return

        if ceph.get_osd_count() == 0:
            logger.info("Storage not available, deferring event.")
            event.defer()
            return

        logger.info("_on_ceph_peers event")

        # Mon addrs might have changed, update the relation data.
        # Additionally, new nodes may have been added, which could be added to
        # NFS clusters.
        self.on.ceph_nfs_reconcile.emit(event.relation)


class CephNfsProviderHandler(RelationHandler):
    """Handler for the ceph-nfs relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self) -> Object:
        """Configure event handlers for an ceph-nfs-client interface."""
        logger.debug("Setting up ceph-nfs-client event handler")

        ceph_nfs = CephNfsProvides(self.charm, self.relation_name)
        self.framework.observe(ceph_nfs.on.ceph_nfs_connected, self._on_ceph_nfs_connected)
        self.framework.observe(ceph_nfs.on.ceph_nfs_reconcile, self._on_ceph_nfs_reconcile)
        self.framework.observe(ceph_nfs.on.ceph_nfs_departed, self._on_ceph_nfs_departed)

        return ceph_nfs

    @property
    def ready(self) -> bool:
        """Check if handler is ready."""
        relations = self.model.relations[self.relation_name]
        if not relations:
            return True

        if not microceph.microceph_has_service("nfs"):
            logger.error("NFS relation found, but snap does not have NFS support.")
            return False

        return True

    def set_status(self, status: compound_status.Status) -> None:
        """Set the status based on current state."""
        relations = self.model.relations[self.relation_name]
        if not relations:
            # If there are no ceph-nfs relations, no need to block this.
            status.set(ActiveStatus(""))
            return

        if not microceph.microceph_has_service("nfs"):
            logger.error("NFS relation found, but snap does not have NFS support.")
            status.set(BlockedStatus("microceph snap does not have NFS support"))
            return

        status.set(ActiveStatus(""))

    def _cluster_id(self, relation) -> str:
        return relation.app.name

    def _on_ceph_nfs_reconcile(self, event: EventBase) -> None:
        if not self.model.unit.is_leader():
            return

        logger.info("Processing ceph-nfs reconcile event")

        # Mon addrs might have changed, update the relation data if needed.
        # Additionally, new nodes may have been added, which could be added to
        # NFS clusters.
        for relation in self.model.relations[self.relation_name]:
            if not self._service_relation(relation):
                logger.error("A ceph-nfs relation could not be serviced.")
                self.status.set(
                    BlockedStatus("A ceph-nfs relation could not be serviced. Check logs.")
                )
                event.defer()
                return

        self.status.set(ActiveStatus(""))

    def _on_ceph_nfs_connected(self, event: EventBase) -> None:
        if not self.model.unit.is_leader():
            return

        logger.info("Processing ceph-nfs connected")
        if not self._service_relation(event.relation):
            logger.error("An error occurred while handling the ceph-nfs relation, deferring.")
            self.status.set(
                BlockedStatus("A ceph-nfs relation could not be serviced. Check logs.")
            )
            event.defer()
            return

    def _service_relation(self, relation) -> bool:
        cluster_id = self._cluster_id(relation)
        relation_data = relation.data[self.model.app]

        if not self._ensure_nfs_cluster(cluster_id):
            # If we can't ensure even 1 node for the NFS cluster, clear the
            # relation data, as it wouldn't be usable.
            relation_data.clear()
            return False

        try:
            ceph.enable_mgr_module("microceph")
            ceph.set_orch_backend("microceph")
        except Exception as ex:
            # If the following commands fail, some clients (e.g.: manila) may
            # not be able to properly provision shares.
            logger.error("Encountered exception: %s", ex)
            relation_data.clear()
            return False

        volume_name = f"{cluster_id}-vol"
        self._ensure_fs_volume(volume_name)

        client_name = f"client.{relation.app.name}"
        caps = {"mon": ["allow r"], "mgr": ["allow rw"]}
        client_key = ceph.get_named_key(client_name, caps)
        addrs = utils.get_mon_addresses()

        relation_data.update(
            {
                "client": client_name,
                "keyring": client_key,
                "mon_hosts": json.dumps(addrs),
                "cluster-id": cluster_id,
                "volume": volume_name,
                "fsid": utils.get_fsid(),
            }
        )

        return True

    def _ensure_nfs_cluster(self, cluster_id) -> bool:
        client = Client.from_socket()
        services = client.cluster.list_services()

        all_nfs_services = [s for s in services if s["service"] == "nfs"]
        nfs_services = [s for s in all_nfs_services if s.get("group_id") == cluster_id]
        nodes_in_cluster = len(nfs_services)

        if nodes_in_cluster >= 3:
            # We're only adding up to 3 nodes in the cluster.
            logger.info(
                "NFS Cluster '%s' already exists, and there are >= 3 nodes in it.", cluster_id
            )
            return True

        # Find potential candidates for the NFS cluster. We can only enable
        # NFS once per host.
        exclude_hosts = [s["location"] for s in all_nfs_services]

        all_hosts = set([s["location"] for s in services])
        candidates = [h for h in all_hosts if h not in exclude_hosts]

        for candidate in candidates:
            try:
                public_addr = self._get_public_address(candidate)
                if not public_addr:
                    logger.warning(
                        "Could not find the public address of '%s' in the peer relation data.",
                        candidate,
                    )
                    continue

                microceph.enable_nfs(candidate, cluster_id, public_addr)

                nodes_in_cluster += 1
                if nodes_in_cluster == 3:
                    break
            except Exception as ex:
                logger.error(
                    "Could not enable nfs (cluster_id '%s') on host '%s': %s",
                    cluster_id,
                    candidate,
                    ex,
                )

        if nodes_in_cluster == 0:
            logger.error("Could not create NFS Cluster '%s' on any host", cluster_id)
            return False

        if nodes_in_cluster < 3:
            logger.warning(
                "NFS cluster '%s' is enabled only on %d / 3 nodes.", cluster_id, nodes_in_cluster
            )
            return True

        logger.info("NFS cluster '%s' is enabled on 3 / 3 nodes.", cluster_id)
        return True

    def _get_public_address(self, hostname: str) -> str:
        rel = self.model.get_relation("peers")
        for unit in itertools.chain(rel.units, [self.model.unit]):
            rel_data = rel.data[unit]
            unit_hostname = rel_data.get(unit.name)

            if hostname == unit_hostname:
                return rel_data.get("public-address")

        return ""

    def _ensure_fs_volume(self, volume_name: str) -> None:
        """Create the FS Volume if it doesn't exist."""
        fs_volumes = ceph.list_fs_volumes()
        for fs_volume in fs_volumes:
            if fs_volume["name"] == volume_name:
                return

        ceph.create_fs_volume(volume_name)

    def _on_ceph_nfs_departed(self, event: EventBase) -> None:
        if not self.model.unit.is_leader():
            return

        logger.info("Processing ceph-nfs departed")

        cluster_id = self._cluster_id(event.relation)
        self._remove_nfs_cluster(cluster_id)

        client_name = f"client.{event.relation.app.name}"
        ceph.remove_named_key(client_name)

        # Because a relation departed, that means the nodes associated with it
        # are now free, which means that we can allocate them to the other NFS
        # clusters as needed.
        other_relations = [
            r for r in self.model.relations[self.relation_name] if r != event.relation
        ]

        error = False
        for relation in other_relations:
            if not self._service_relation(relation):
                logger.error("An error occurred while handling the ceph-nfs relation, deferring.")
                self.status.set(
                    BlockedStatus("A ceph-nfs relation could not be serviced. Check logs.")
                )
                error = True

        if not error:
            self.status.set(ActiveStatus(""))

    def _remove_nfs_cluster(self, cluster_id):
        client = Client.from_socket()
        services = client.cluster.list_services()
        nfs_services = [
            s for s in services if s["service"] == "nfs" and s.get("group_id") == cluster_id
        ]

        for service in nfs_services:
            host = service["location"]
            try:
                microceph.disable_nfs(host, cluster_id)
            except Exception as ex:
                logger.error(
                    "Could not disable nfs (cluster_id '%s') on host '%s': %s",
                    cluster_id,
                    host,
                    ex,
                )
                raise
