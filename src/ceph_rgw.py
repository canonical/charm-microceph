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

"""Handle Charm's RGW Client Provider."""

import logging

from ops.charm import CharmBase, RelationEvent
from ops_sunbeam.relation_handlers import ServiceReadinessProviderHandler

import ceph

logger = logging.getLogger(__name__)

CEPH_RGW_READY_RELATION = "ceph-rgw-ready"


class CephRgwProviderHandler(ServiceReadinessProviderHandler):
    """Handler for the ceph-rgw-ready relation."""

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name, self.handle_readiness_request_from_event)

    def handle_readiness_request_from_event(self, event: RelationEvent) -> None:
        """Set service readiness in relation data."""
        self.interface.set_service_status(event.relation, self.rgw_ready)

    def set_readiness_on_related_units(self) -> None:
        """Set service readiness on ceph-rgw-ready related units."""
        logger.debug("Set service readiness on all connected placement relations")
        ready = self.rgw_ready
        for relation in self.model.relations.get(CEPH_RGW_READY_RELATION, []):
            logger.debug(f"Setting rgw readiness to {ready} on relation {relation.id}")
            self.interface.set_service_status(relation, ready)

    @property
    def rgw_ready(self) -> bool:
        """Returns whether rgw is ready."""
        if not self.charm.config.get("enable-rgw"):
            return False

        if not self.charm.ready_for_service():
            return False

        osd_count = ceph.get_osd_count()
        if osd_count == 0:
            return False

        if osd_count < self.charm.config.get("default-pool-size"):
            return False

        service_status = ceph.CephStatus().service_status()
        rgw_services = service_status.get("rgw", {})
        if len(rgw_services.keys()) == 0:
            return False

        return True
