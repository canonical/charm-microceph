#!/usr/bin/env python3

# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""CephClientRequirerMock Charm.

This charm deploys ceph client interface to test
microceph charm.
"""

import json

import interface_ceph_client.ceph_client as ceph_client
from ops.charm import CharmBase
from ops.framework import EventBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

CEPH_RGW_RELATION = "ceph-rgw-ready"


class CephClientRequirerMock(CharmBase):
    def __init__(self, framework) -> None:
        super().__init__(framework)
        self.ceph = ceph_client.CephClientRequires(
            self,
            "ceph",  # relation name
        )
        self.framework.observe(self.ceph.on.broker_available, self.request_pools)

        self.framework.observe(
            self.on[CEPH_RGW_RELATION].relation_changed,
            self._on_rgw_ready_changed,
        )
        self.framework.observe(
            self.on[CEPH_RGW_RELATION].relation_broken,
            self._on_rgw_ready_broken,
        )

        self.unit.status = ActiveStatus("")

    def request_pools(self, event: EventBase) -> None:
        self.ceph.create_replicated_pool(
            name=f"{self.app.name}-metadata",
            replicas=3,
            weight=40 * 0.01,
            app_name=f"{self.app.name}",
        )

    def _on_rgw_ready_changed(self, event: EventBase) -> None:
        if self._is_rgw_ready():
            self.unit.status = ActiveStatus("")
        else:
            self.unit.status = BlockedStatus("RGW is not ready")

    def _on_rgw_ready_broken(self, event: EventBase) -> None:
        self.unit.status = ActiveStatus("")

    def _get_remote_app_data(self, relation_name: str, key: str) -> str | None:
        """Return the value for the given key from remote app data."""
        rel = self.framework.model.get_relation(relation_name)
        if not rel:
            return None

        data = rel.data[rel.app]
        return data.get(key)

    def _is_rgw_ready(self) -> bool:
        """Return if rgw service is ready or not."""
        is_ready = self._get_remote_app_data(CEPH_RGW_RELATION, "ready")
        if is_ready:
            return json.loads(is_ready)

        return False


if __name__ == "__main__":
    main(CephClientRequirerMock)
