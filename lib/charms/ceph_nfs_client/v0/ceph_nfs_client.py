"""CephNfsRequires module.

This library contains the Requires class for handling the ceph-nfs-client
interface.

Import `CephNfsRequires` in your charm, with the charm object and the relation
name:
    - self
    - "ceph-nfs"

Two events are also available to respond to:
    - connected
    - departed

A basic example showing the usage of this relation follows:

```
import charms.ceph_nfs.v0.ceph_nfs as ceph_nfs


class CephNfsClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # CephNfsRequires
        self._ceph_nfs = ceph_nfs.CephNfsRequires(
            self, "ceph-nfs",
        )
        self.framework.observe(
            self._ceph_nfs.on.connected,
            self._on_ceph_nfs_connected,
        )
        self.framework.observe(
            self._ceph_nfs.on.departed,
            self._on_ceph_nfs_departed,
        )

    def _on_ceph_nfs_connected(self, event):
        '''React to the CephNfsConnectedEvent event.

        This event happens when the ceph-nfs relation is added to the
        model before information has been provided.
        '''
        # Do something before the relation is complete.
        pass

    def _on_ceph_nfs_departed(self, event):
        '''React to the CephNfsDepartedEvent event.

        This event happens when ceph-nfs relation is removed.
        '''
        # ceph-nfs relation has departed. Shutdown services if needed.
        pass
```
"""

import json
import logging

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationEvent,
)
from ops.framework import (
    EventSource,
    Object,
    ObjectEvents,
)
from ops.model import (
    Relation,
)

# The unique Charmhub library identifier, never change it
LIBID = "0fb2d24550d94d24868d3cefa784d5be"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

logger = logging.getLogger(__name__)


class CephNfsConnectedEvent(RelationEvent):
    """ceph-nfs connected event."""

    pass


class CephNfsDepartedEvent(RelationEvent):
    """ceph-nfs relation departed event."""

    pass


class CephNfsEvents(ObjectEvents):
    """Events class for `on`."""

    ceph_nfs_connected = EventSource(CephNfsConnectedEvent)
    ceph_nfs_departed = EventSource(CephNfsDepartedEvent)


class CephNfsRequires(Object):
    """CephNfsRequires class."""

    on = CephNfsEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ceph_nfs_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_ceph_nfs_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ceph_nfs_relation_broken,
        )

    def _on_ceph_nfs_relation_changed(self, event: RelationChangedEvent):
        """Handle ceph-nfs relation changed."""
        logging.debug("ceph-nfs relation changed")
        self.on.ceph_nfs_connected.emit(event.relation)

    def _on_ceph_nfs_relation_broken(self, event: RelationBrokenEvent):
        """Handle ceph-nfs relation broken."""
        logging.debug("ceph-nfs relation broken")
        self.on.ceph_nfs_departed.emit(event.relation)

    @property
    def _ceph_nfs_rel(self) -> Relation | None:
        """The ceph-nfs relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_relation_data(self) -> dict:
        """Get the ceph-nfs relation data."""
        if not self._ceph_nfs_rel:
            return {}

        relation_data = self._ceph_nfs_rel.data[self._ceph_nfs_rel.app]
        if not relation_data:
            return {}

        mon_hosts = json.loads(relation_data["mon-hosts"])

        return {
            "client": relation_data["client"],
            "keyring": relation_data["keyring"],
            "mon_hosts": mon_hosts,
            "cluster-id": relation_data["cluster-id"],
            "volume": relation_data["volume"],
            "fsid": relation_data["fsid"],
        }
