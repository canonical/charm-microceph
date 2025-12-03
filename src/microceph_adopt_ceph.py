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
    """adopt-ceph bootstrap event."""

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
        """On relation changed."""
        if not self.model.unit.is_leader():
            logger.debug("Unit is not leader, skipping adopt-ceph changed event")
            return

        # Do nothing if already bootstrapped
        if self.charm.ready_for_service():
            logger.debug("Not processing adopt relation event, microceph already bootstrapped.")
            return

        logger.debug("Emitting adopt-ceph reconcile event")
        self.on.adopt_ceph_bootstrap.emit(event.relation)

    def _on_relation_broken(self, event) -> None:
        """On relation departed."""
        if not self.model.unit.is_leader():
            logger.debug("Unit is not leader, skipping adopt-ceph departed event")
            return

        with sunbeam_guard.guard(self.charm, self.relation_name):
            if not self.charm.ready_for_service():
                raise sunbeam_guard.BlockedExceptionError(
                    "Adopt relation removed before cluster bootstrap could be performed"
                )

        logger.debug("Ignoring adopt-ceph departed event")


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

    def _on_bootstrap(self, relation):
        """Bootstrap MicroCeph cluster using adopted ceph cluster."""
        logger.info("Handling adopt-ceph bootstrap event")
        with sunbeam_guard.guard(self.charm, self.relation_name):
            for relation in self.model.relations.get(self.relation_name, []):
                if not relation.units:
                    logger.debug("No units in adopt-ceph relation, cannot reconcile")
                    return

                remote_ceph_data = relation.data.get(next(iter(relation.units)), {})
                logger.debug(f"Adopt-ceph relation data: IsEmpty({remote_ceph_data is None})")

                # fetched mon hosts value is a space separated string of host addresses.
                mon_hosts = remote_ceph_data.get(AdoptCephRelationDataKeys.mon_hosts.value, None)
                fsid = remote_ceph_data.get(AdoptCephRelationDataKeys.fsid.value, None)
                admin_key = remote_ceph_data.get(AdoptCephRelationDataKeys.admin_key.value, None)

                logger.debug(
                    f"Adopt-ceph relation data fetched: fsid({fsid}), mon_hosts({mon_hosts}), admin_key({admin_key is not None})"
                )

                if not mon_hosts or not fsid or not admin_key:
                    logger.debug("Incomplete data from adopt-ceph relation, cannot reconcile")
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Waiting for fsid({fsid}), mon_hosts({mon_hosts}) and admin_key({admin_key is not None}) from adopt-ceph relation"
                    )

                logger.debug(
                    "All required data from adopt-ceph relation present, proceeding with adoption"
                )
                self.charm.adopt_cluster(fsid, mon_hosts.split(), admin_key)
                self.callback_f(event=relation)
                return
