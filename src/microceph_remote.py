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

import logging
from enum import Enum
from typing import Callable

import ops_sunbeam.guard as sunbeam_guard
from ops.charm import CharmBase, RelationEvent
from ops.framework import (
    EventSource,
    Object,
    ObjectEvents,
)
from ops_sunbeam.relation_handlers import RelationHandler

import microceph

logger = logging.getLogger(__name__)


class RemoteRelationDataKeys(Enum):
    """Keys for the remote relation."""

    site_name = "site-name"
    token = "token"


class MicrocephRemoteDepartedEvent(RelationEvent):
    """Remote departed event."""

    pass


class MicrocephRemoteReconcileEvent(RelationEvent):
    """Remote reconcile event for absorbing remote application updates."""

    pass


class MicrocephRemoteUpdateRemoteEvent(RelationEvent):
    """Remote update event for updating remote application."""

    pass


class MicrocephRemoteEvent(ObjectEvents):
    """Remote events."""

    microceph_remote_departed = EventSource(MicrocephRemoteDepartedEvent)
    microceph_remote_reconcile = EventSource(MicrocephRemoteReconcileEvent)
    microceph_remote_update_remote = EventSource(MicrocephRemoteUpdateRemoteEvent)


class MicroCephRemote(Object):
    """Interface for Remote integration."""

    # register events for handler to consume
    on = MicrocephRemoteEvent()

    def __init__(self, charm, relation_name="remote") -> None:
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(charm.on[relation_name].relation_joined, self._on_changed)
        self.framework.observe(charm.on[relation_name].relation_broken, self._on_broken)
        self.framework.observe(charm.on[relation_name].relation_changed, self._on_changed)

        # React to ceph peers to update
        self.framework.observe(charm.on["peers"].relation_departed, self._on_peer_updated)
        self.framework.observe(charm.on["peers"].relation_changed, self._on_peer_updated)

    def _on_broken(self, event):
        if not self.model.unit.is_leader():
            logger.debug("Not the leader, ignoring remote event")
            return

        with sunbeam_guard.guard(self.charm, self.relation_name):
            is_ready = self.charm.ready_for_service()
            if not is_ready:
                logger.debug("Microceph not ready, deferring remote relation changed event")
                event.defer()
                raise sunbeam_guard.WaitingExceptionError(f"cluster not ready: {is_ready}")

            # TODO: (utkarshbhatthere):
            # When specific workload based integrations are implemented,
            # add a check here to go to blocked state if such a relation exists.

            # remove remote record
            logger.debug("Emit: Remote departed event")
            self.on.microceph_remote_departed.emit(event.relation)

    def _on_changed(self, event):
        if not self.model.unit.is_leader():
            logger.debug("Not the leader, ignoring remote event")
            return
        site_name = self.charm.model.config.get("site-name")

        with sunbeam_guard.guard(self.charm, self.relation_name):
            is_ready = self.charm.ready_for_service()
            if not is_ready:
                logger.debug("Microceph not ready, deferring remote relation changed event")
                event.defer()
                raise sunbeam_guard.WaitingExceptionError(f"cluster not ready: {is_ready}")

            if not site_name:
                logger.debug("Blocking remote relation, site-name not set")
                event.defer()
                raise sunbeam_guard.BlockedExceptionError("config site-name not set")

            logger.debug("Processing remote relation changed event")

            local_relation_data = event.relation.data.get(self.charm.app)
            remote_relation_data = event.relation.data.get(event.relation.app)

            # Check local data
            local_site_name = local_relation_data.get(RemoteRelationDataKeys.site_name.value, None)
            local_token = local_relation_data.get(RemoteRelationDataKeys.token.value, None)

            logger.debug(
                "Data for local site: Name=%s, isToken=%s",
                local_site_name,
                local_token is not None,
            )

            if not local_site_name or not local_token:
                logger.debug("Emit: Remote update remote event")
                self.on.microceph_remote_update_remote.emit(event.relation)

            # Check remote data
            remote_site_name = remote_relation_data.get(
                RemoteRelationDataKeys.site_name.value, None
            )
            remote_token = remote_relation_data.get(RemoteRelationDataKeys.token.value, None)

            logger.debug(
                "Data for remote site: Name=%s, isToken=%s",
                remote_site_name,
                remote_token is not None,
            )

            if remote_site_name is not None and remote_token is not None:
                # remote data available, reconcile if necessary
                logger.debug("Emit: Remote reconcile event")
                self.on.microceph_remote_reconcile.emit(event.relation)

    def _on_peer_updated(self, event):
        if not self.model.unit.is_leader():
            logger.debug("Not the leader, ignoring remote update event")
            return

        if not self.charm.model.config.get("site-name", None):
            # site name is not set
            logger.debug("site name not set, skipping remote update due to peer update")
            return

        for remote_rel in self.charm.model.relations[self.relation_name]:
            logger.debug("Emit: Remote update remote remote due to peer update")
            self.on.microceph_remote_update_remote.emit(remote_rel)


class MicroCephRemoteHandler(RelationHandler):
    """Handler for remote integration."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self) -> Object:
        """Configure event handlers for remote interface."""
        logger.debug(f"Setting up {self.relation_name} event handler")

        microceph_remote = MicroCephRemote(self.charm, self.relation_name)

        self.framework.observe(microceph_remote.on.microceph_remote_departed, self._on_departed)
        self.framework.observe(microceph_remote.on.microceph_remote_reconcile, self._on_reconcile)
        self.framework.observe(
            microceph_remote.on.microceph_remote_update_remote, self._on_update_remote
        )

        return microceph_remote

    @property
    def ready(self) -> bool:
        """Sunbeam interface for readiness check."""
        # Note: (utkarshbhatthere): On provider side this is mandatorily marked True
        # on requirer side, this will be checked before writing configuration files on containers.
        # not-relevant for microceph.
        logger.debug(f"Reporting {self.relation_name} ready")
        return True

    def _on_departed(self, event):
        """Handle integration cleanup."""
        logger.debug("Handling remote departed event")
        remote_relation_data = event.relation.data.get(event.relation.app)
        remote_site_name = remote_relation_data.get(RemoteRelationDataKeys.site_name.value, None)

        remove_remote_cluster(remote_site_name)

    def _on_reconcile(self, event):
        logger.debug("Handling remote reconcile event")
        # fetch remote app data
        remote_relation_data = event.relation.data.get(event.relation.app, None)
        remote_site_name = remote_relation_data.get(RemoteRelationDataKeys.site_name.value, None)
        remote_token = remote_relation_data.get(RemoteRelationDataKeys.token.value, None)

        if remote_site_name and remote_token:
            import_remote_cluster(
                local_name=self.charm.model.config.get("site-name"),
                remote_name=remote_site_name,
                remote_token=remote_token,
            )

    def _on_update_remote(self, event):
        logger.debug("Handling remote update event")

        local_relation_data = event.relation.data.get(self.charm.app)
        remote_relation_data = event.relation.data.get(event.relation.app)

        local_site_name = local_relation_data.get(RemoteRelationDataKeys.site_name.value, None)
        if not local_site_name:
            local_site_name = self.charm.model.config.get("site-name")
            logger.debug("Updating local site name(%s) in remote relation data", local_site_name)
            local_relation_data.update({RemoteRelationDataKeys.site_name.value: local_site_name})

        local_token = local_relation_data.get(RemoteRelationDataKeys.token.value, None)
        remote_site_name = remote_relation_data.get(RemoteRelationDataKeys.site_name.value, None)

        logger.debug(
            "For Remote site: %s, isLocalToken=%s",
            remote_site_name,
            local_token is not None,
        )
        # remote site name is required to generate token
        if not local_token and remote_site_name:
            new_token = get_cluster_export_token(remote_site_name)
            local_relation_data.update({RemoteRelationDataKeys.token.value: new_token})


def get_cluster_export_token(remote_name) -> str:
    """Get new cluster token for remote."""
    if not remote_name:
        raise sunbeam_guard.BlockedExceptionError("Remote site name empty")

    return microceph.export_cluster_token(remote_name)


def import_remote_cluster(local_name, remote_name, remote_token) -> None:
    """Import remote cluster using provided token."""
    if not remote_token or not remote_name or not local_name:
        logger.error(
            f"Aborting remote import, all values from remote name({remote_name}), local name({local_name}) and token required"
        )
        return

    logger.debug(f"Importing remote cluster {remote_name} into local cluster {local_name}")

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

    logger.debug(f"Removing remote cluster {remote_name}")

    microceph.remove_remote_cluster(
        remote_name=remote_name,
    )
