"""CephCSIRequires module.

This library contains the Requires class for handling the ceph-csi
interface.

Import `CephCSIRequires` in your charm, with the charm object and the relation
name:
    - self
    - "ceph-csi"

Three events are also available to respond to:
    - ceph_csi_available
    - ceph_csi_connected
    - ceph_csi_departed

A basic example showing the usage of this relation follows:

```
import charms.ceph_csi.v0.ceph_csi as ceph_csi


class CephCSIClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self._ceph_csi = ceph_csi.CephCSIRequires(self, "ceph-csi")
        self.framework.observe(
            self._ceph_csi.on.ceph_csi_available,
            self._on_ceph_csi_available,
        )
        self.framework.observe(
            self._ceph_csi.on.ceph_csi_connected,
            self._on_ceph_csi_connected,
        )
        self.framework.observe(
            self._ceph_csi.on.ceph_csi_departed,
            self._on_ceph_csi_departed,
        )

    def _on_ceph_csi_available(self, event):
        # Request workloads when relation is available
        self._ceph_csi.request_workloads(["rbd", "cephfs"])

    def _on_ceph_csi_connected(self, event):
        # Relation data is ready for use
        data = self._ceph_csi.get_relation_data()
        pass

    def _on_ceph_csi_departed(self, event):
        # Relation removed
        pass
```
"""

import json
import logging
from typing import Iterable, List

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationEvent,
)
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import Relation

# The unique Charmhub library identifier, never change it
LIBID = "9e5c2d9bb7004a1bb4d8b4b6d7e9d5c8"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

logger = logging.getLogger(__name__)


class CephCSIConnectedEvent(RelationEvent):
    """ceph-csi connected event."""


class CephCSIAvailableEvent(RelationEvent):
    """ceph-csi available event."""


class CephCSIDepartedEvent(RelationEvent):
    """ceph-csi relation departed event."""


class CephCSIEvents(ObjectEvents):
    """Events class for `on`."""

    ceph_csi_available = EventSource(CephCSIAvailableEvent)
    ceph_csi_connected = EventSource(CephCSIConnectedEvent)
    ceph_csi_departed = EventSource(CephCSIDepartedEvent)


class CephCSIRequires(Object):
    """CephCSIRequires class."""

    on = CephCSIEvents()  # type: ignore[assignment]

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ceph_csi_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ceph_csi_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_ceph_csi_relation_broken,
        )

    def _on_ceph_csi_relation_changed(self, event: RelationChangedEvent):
        """Handle ceph-csi relation changed."""
        logger.debug("ceph-csi relation changed")
        if self._is_data_ready(event.relation):
            self.on.ceph_csi_connected.emit(event.relation)
            return
        self.on.ceph_csi_available.emit(event.relation)

    def _on_ceph_csi_relation_broken(self, event: RelationBrokenEvent):
        """Handle ceph-csi relation broken."""
        logger.debug("ceph-csi relation broken")
        self.on.ceph_csi_departed.emit(event.relation)

    def _is_data_ready(self, relation: Relation) -> bool:
        relation_data = relation.data.get(relation.app, {})
        if not relation_data:
            return False
        required_keys = ("fsid", "mon_hosts", "user_id", "user_key")
        return all(relation_data.get(key) for key in required_keys)

    @property
    def _relation(self) -> Relation | None:
        """The ceph-csi relation."""
        return self.framework.model.get_relation(self.relation_name)

    def request_workloads(self, workloads: Iterable[str]) -> None:
        """Request workloads to be enabled for the client."""
        if not self._relation or not self.model.unit.is_leader():
            return

        payload = json.dumps(list(workloads))
        self._relation.data[self.model.app]["workloads"] = payload

    def get_relation_data(self) -> dict:
        """Get relation data for ceph-csi."""
        if not self._relation or not self._relation.app:
            return {}

        relation_data = self._relation.data[self._relation.app]
        if not relation_data:
            return {}

        return {
            "fsid": relation_data.get("fsid"),
            "mon_hosts": json.loads(relation_data.get("mon_hosts", "[]")),
            "rbd_pool": relation_data.get("rbd_pool"),
            "cephfs_fs_name": relation_data.get("cephfs_fs_name"),
            "user_id": relation_data.get("user_id"),
            "user_key": relation_data.get("user_key"),
        }
