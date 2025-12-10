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

"""Handle Charm's Adopt Ceph Integration Events."""

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

logger = logging.getLogger(__name__)


class AdoptCephRelationDataKeys(Enum):
    """Relation Data keys."""

    mon_hosts = "mon_hosts"
    admin_key = "key"
    fsid = "fsid"


class AdoptCephBootstrapEvent(RelationEvent):
    """Event emitted when adopt-ceph relation provides complete cluster credentials.

    This event signals that all necessary information (monitor hosts, admin key,
    and fsid) has been received from the adopt-ceph relation, and the charm can
    proceed with the cluster bootstrap process by adopting the external Ceph cluster.
    """

    pass


class AdoptCephEvents(ObjectEvents):
    """Events for adopt-ceph relation handler."""

    adopt_ceph_bootstrap = EventSource(AdoptCephBootstrapEvent)


class AdoptCephRequires(Object):
    """Interface for ceph-admin interface."""

    on = AdoptCephEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = "adopt-ceph",
    ):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(charm.on[relation_name].relation_changed, self._on_relation_changed)
        self.framework.observe(charm.on[relation_name].relation_broken, self._on_relation_broken)
        self.framework.observe(charm.on[relation_name].relation_joined, self._on_relation_changed)

    def _on_relation_changed(self, event) -> None:
        """Trigger adopt-ceph bootstrap event if required."""
        if not self.model.unit.is_leader():
            logger.debug("Unit is not leader, skipping adopt-ceph changed event")
            return

        # Do nothing if already bootstrapped
        if self.charm.ready_for_service():
            logger.info("Not processing adopt relation event, microceph already bootstrapped.")
            return

        logger.debug("Emitting adopt-ceph reconcile event")
        self.on.adopt_ceph_bootstrap.emit(event.relation)

    def _on_relation_broken(self, _event) -> None:
        """Mark status blocked if adopt relation removed before bootstrap."""
        if not self.model.unit.is_leader():
            logger.debug("Unit is not leader, skipping adopt-ceph broken event")
            return

        with sunbeam_guard.guard(self.charm, self.relation_name):
            if not self.charm.ready_for_service():
                raise sunbeam_guard.BlockedExceptionError(
                    "Adopt relation removed before cluster bootstrap could be performed"
                )

        logger.debug("Ignoring adopt-ceph broken event")


class AdoptCephRequiresHandler(RelationHandler):
    """Handler for adopt-ceph relation events."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    @property
    def ready(self) -> bool:
        """Check if adopt-ceph relation is ready."""
        logger.info(f"Report {self.relation_name} as ready")
        return True

    def setup_event_handler(self) -> Object:
        """Configure event handlers for a ceph-admin interface."""
        logger.debug("Setting up adopt-ceph event handler")

        self.adopt_ceph = AdoptCephRequires(self.charm, self.relation_name)

        self.framework.observe(self.adopt_ceph.on.adopt_ceph_bootstrap, self._on_bootstrap)

    def _on_bootstrap(self, relation_event):
        """Bootstrap MicroCeph cluster using adopted ceph cluster."""
        logger.info("Handling adopt-ceph bootstrap event")
        with sunbeam_guard.guard(self.charm, self.relation_name):
            relations = self.model.relations.get(self.relation_name, [])
            if not relations:
                logger.warning("No adopt-ceph relations found during bootstrap.")
                return

            # choose the first relation as the bootstrap source.
            relation = None
            for rel in relations:
                if not rel.units:
                    logger.debug("No units in adopt-ceph relation, cannot reconcile")
                    continue

                relation = rel

            if not relation:
                logger.debug("No valid adopt-ceph relation with units found to reconcile")
                return

            # since the ceph-mon units put their individual data in the relation.
            unit = next(iter(relation.units), None)
            if unit is None:
                logger.debug("No units available in adopt-ceph relation after check")
                return
            remote_ceph_data = relation.data.get(unit, {})
            logger.debug(f"Adopt-ceph relation data: IsEmpty({remote_ceph_data is None})")

            # fetched mon hosts value is a space separated string of host addresses.
            mon_hosts = remote_ceph_data.get(AdoptCephRelationDataKeys.mon_hosts.value, None)
            fsid = remote_ceph_data.get(AdoptCephRelationDataKeys.fsid.value, None)
            admin_key = remote_ceph_data.get(AdoptCephRelationDataKeys.admin_key.value, None)

            logger.info(
                f"Adopt-ceph relation data fetched: fsid({fsid}), mon_hosts({mon_hosts}), admin_key({admin_key is not None})"
            )

            if not mon_hosts or not fsid or not admin_key:
                logger.debug("Incomplete data from adopt-ceph relation, cannot reconcile")
                raise sunbeam_guard.BlockedExceptionError(
                    f"Waiting for fsid({fsid}), mon_hosts({mon_hosts}) and admin_key({admin_key is not None}) from adopt-ceph relation"
                )

            # Split mon_hosts and filter out empty strings
            mon_hosts_list = [host for host in mon_hosts.split() if host]
            if not mon_hosts_list:
                logger.debug("No valid mon_hosts found after splitting")
                raise sunbeam_guard.BlockedExceptionError(
                    f"Invalid mon_hosts from adopt-ceph relation: {mon_hosts!r}"
                )

            logger.debug(
                "All required data from adopt-ceph relation present, proceeding with adoption"
            )
            self.charm.adopt_cluster(fsid, mon_hosts_list, admin_key)
            self.callback_f(event=relation_event)
            return
