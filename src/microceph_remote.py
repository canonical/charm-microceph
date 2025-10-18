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


"""Handle Charm's Remote Integration Events."""

import base64
import configparser
import json
from enum import Enum
import logging
from typing import Callable


import ops_sunbeam.guard as sunbeam_guard
from ops.charm import CharmBase, RelationEvent
from ops.framework import (
    EventSource,
    Object,
    ObjectEvents,
    StoredState,
)
from ops_sunbeam.relation_handlers import RelationHandler

import microceph

logger = logging.getLogger(__name__)


class RemoteRelationDataKeys(Enum):
    site_name = "site-name"
    monitors = "monitors"
    token = "token"


class MicrocephRemoteDepartedEvent(RelationEvent):
    """remote departed event"""

    pass


class MicrocephRemoteReconcileEvent(RelationEvent):
    """remote reconcile event for absorbing remote application updates"""

    pass


class MicrocephRemoteUpdateEvent(RelationEvent):
    """remote update event for updating remote application"""

    pass


class MicrocephRemoteEvent(ObjectEvents):
    microceph_remote_departed = EventSource(MicrocephRemoteDepartedEvent)
    microceph_remote_reconcile = EventSource(MicrocephRemoteReconcileEvent)
    microceph_remote_update = EventSource(MicrocephRemoteUpdateEvent)


class MicroCephRemote(Object):
    """Interface for Remote provider"""

    # register events for handler to consume
    on = MicrocephRemoteEvent()

    def __init__(self, charm, relation_name="remote") -> None:
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(charm.on[relation_name].joined, self._on_changed)
        self.framework.observe(charm.on[relation_name].departed, self._on_departed)
        self.framework.observe(charm.on[relation_name].changed, self._on_changed)

        # React to ceph peers to update
        self.framework.observe(
            charm.on["peers"].relation_departed, self._on_peer_updated
        )
        self.framework.observe(
            charm.on["peers"].relation_changed, self._on_peer_updated
        )

    def _on_departed(self, event):
        if not self.model.unit.is_leader():
            logger.debug("Not the leader, ignoring remote event")
            return

        # TODO: (utkarshbhatthere):
        # When specific workload based integrations are implemented,
        # add a check here to go to blocked state if such a relation exists.

        # remove remote record
        self.on.microceph_remote_departed.emit(event)

    def _on_changed(self, event):
        if not self.model.unit.is_leader():
            logger.debug("Not the leader, ignoring remote event")
            return

        with sunbeam_guard.guard(self.charm, self.relation_name):
            site_name = self.charm.model.config.get("site-name")

            if not site_name:
                event.defer()
                raise sunbeam_guard.BlockedExceptionError("config site-name not set")

            local_cluster_data = event.relation.data.get(self.charm.app)

            # populate local site name if not added.
            if not local_cluster_data.get(RemoteRelationDataKeys.site_name, None):
                local_cluster_data.update({RemoteRelationDataKeys.site_name: site_name})

            local_token = local_cluster_data.get(RemoteRelationDataKeys.token, None)
            if not local_token:
                # emit event to update self data in databag.
                self.on.microceph_remote_update.emit(event)

            remote_relation_data = event.relation.data.get(event.app)
            remote_site_name = remote_relation_data.get(
                RemoteRelationDataKeys.site_name, None
            )
            remote_token = remote_relation_data.get(RemoteRelationDataKeys.token, None)
            if remote_site_name is not None and remote_token is not None:
                # remote data available, reconcile if necessary
                self.on.microceph_remote_reconcile.emit(event.relation)

    def _on_peer_updated(self, event):
        if not self.model.unit.is_leader():
            logger.debug("Not the leader, ignoring remote update event")
            return

        site_name = self.charm.model.config.get("site-name", None)

        if not site_name:
            # site name is not set
            return

        self.emit.microceph_remote_update.emit(event)


class MicroCephRemoteHandler(RelationHandler):
    """Handler for remote integration"""

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

        microceph_remote = MicroCephRemote(self.charm, self.relation_name)

        self.framework.observe(
            microceph_remote.on.microceph_remote_departed, self._on_departed
        )
        self.framework.observe(
            microceph_remote.on.microceph_remote_reconcile, self._on_reconcile
        )
        self.framework.observe(
            microceph_remote.on.microceph_remote_update, self._on_updated
        )

        return microceph_remote

    @property
    def ready(self) -> bool:
        return True

    def _on_departed(self, event):
        """handle integration cleanup"""
        remote_relation_data = event.relation.data.get(event.app)
        remote_site_name = remote_relation_data.get(
            RemoteRelationDataKeys.site_name, None
        )

        remove_remote_cluster(remote_site_name)

    def _on_reconcile(self, event):
        # fetch remote app data
        remote_relation_data = event.relation.data.get(event.app)
        remote_site_name = remote_relation_data.get(
            RemoteRelationDataKeys.site_name, None
        )
        remote_token = remote_relation_data.get(RemoteRelationDataKeys.token, None)
        remote_ceph_monitors = decode_monitors_from_cluster_token(remote_token)
        current_monitors = get_cluster_monitors(remote_site_name)

        # if monitors differ, re-import new token
        if any(monitor not in current_monitors for monitor in remote_ceph_monitors):
            import_remote_cluster(
                local_name=self.charm.model.config.get("site-name"),
                remote_name=remote_site_name,
                remote_token=remote_token,
            )

    def _on_updated(self, event):
        # fetch local app data
        ## TODO: Fix this as event object would not contain the remote relation databag here.
        local_relation_data = event.relation.data.get(self.charm.app)
        remote_relation_data = event.relation.data.get(event.app)

        remote_site_name = remote_relation_data.get(
            RemoteRelationDataKeys.site_name, None
        )
        local_token = local_relation_data.get(RemoteRelationDataKeys.token, None)
        last_updated_monitors = decode_monitors_from_cluster_token(local_token)

        new_token = get_cluster_export_token(remote_site_name)
        current_monitors = decode_monitors_from_cluster_token(new_token)

        # if monitors differ, update token
        if set(last_updated_monitors) != set(current_monitors):
            local_relation_data.update({RemoteRelationDataKeys.token: new_token})


def decode_monitors_from_cluster_token(token) -> list:
    """Decode remote import token to read monitor IPs."""
    if not token:
        return []

    json_b_str = base64.b64decode(token.encode("ascii"))
    import_dict = json.loads(json_b_str.decode("utf-8"))

    return [value for key, value in import_dict.items() if key.startswith("mon.host")]


def get_cluster_monitors(cluster_name) -> list:
    """Get monitors of a given cluster."""
    if not cluster_name:
        return []

    remote_config = configparser.ConfigParser()
    remote_config.read(f"/var/snap/microceph/current/conf/{cluster_name}.conf")

    try:
        monitors = remote_config["global"]["mon host"]
        # work around ipv6 bracket enclosure
        monitors = monitors.replace("[", "")
        monitors = monitors.replace("]", "")

        return monitors.split(",")
    except KeyError:
        return []


def get_cluster_export_token(remote_name) -> str:
    """Get new cluster token for remote."""
    if not remote_name:
        return ""

    return microceph.export_cluster_token(remote_name)


def import_remote_cluster(local_name, remote_name, remote_token) -> None:
    """Import remote cluster using provided token."""
    if not remote_token or not remote_name or not local_name:
        logger.error(
            f"Aborting remote import, all values from remote name({remote_name}), local name({local_name}) and token required"
        )
        return

    microceph.import_remote_token(
        local_name=local_name,
        remote_name=remote_name,
        remote_token=remote_token,
    )


def remove_remote_cluster(remote_name) -> None:
    """Remove remote cluster configuration."""
    if not remote_name:
        logger.error(f"Aborting remote removal, remote name({remote_name}) required")
        return

    microceph.remove_remote_cluster(
        remote_name=remote_name,
    )
