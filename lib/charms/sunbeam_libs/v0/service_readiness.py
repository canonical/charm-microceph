"""Service Provides and Requires module.

The interface `service-ready` is to inform that remote service is ready.
This library contains the Requires and Provides classes for handling
the service-ready interface.

Import `ServiceReadinessRequirer` in your charm, with the charm object and the
relation name:
    - self
    - "service"

Two events are also available to respond to:
    - readiness_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.masakari_k8s.v0.service_readiness import (
    ServiceReadinessRequirer
)

class ServiceClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        #  Service Requires
        self._svc = ServiceReadinessRequirer(
            self, "service",
        )
        self.framework.observe(
            self._svc.on.readiness_changed,
            self._on_service_readiness_changed
        )
        self.framework.observe(
            self._svc.on.goneaway,
            self._on_service_goneaway
        )

    def _on_service_readiness_changed(self, event):
        '''React to the service readiness changed event.

        This event happens when service relation is added to the
        model and relation data is changed.
        '''
        # Do something with the configuration provided by relation.
        pass

    def _on_service_goneaway(self, event):
        '''React to the Service goneaway event.

        This event happens when service relation is removed.
        '''
        # Service Relation has goneaway.
        pass
```
"""


import json
import logging

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
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

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "706872aa869c11ef9444175192825660"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class ServiceReadinessRequestEvent(RelationEvent):
    """ServiceReadinessRequest Event."""

    pass


class ServiceReadinessProviderEvents(ObjectEvents):
    """Events class for `on`."""

    service_readiness = EventSource(ServiceReadinessRequestEvent)


class ServiceReadinessProvider(Object):
    """ServiceReadinessProvider class."""

    on = ServiceReadinessProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle service relation changed."""
        logging.debug(f"Service relation changed for relation {self.relation_name}")
        self.on.service_readiness.emit(event.relation)

    def set_service_status(self, relation: Relation, is_ready: bool) -> None:
        """Set service readiness status on the relation."""
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping setting ready status")
            return

        logging.debug(
            f"Setting ready status on relation {relation.app.name} "
            f"{relation.name}/{relation.id}"
        )
        relation.data[self.charm.app]["ready"] = json.dumps(is_ready)


class ServiceReadinessChangedEvent(RelationEvent):
    """ServiceReadinessChanged Event."""

    pass


class ServiceGoneAwayEvent(RelationEvent):
    """ServiceGoneAway Event."""

    pass


class ServiceReadinessRequirerEvents(ObjectEvents):
    """Events class for `on`."""

    readiness_changed = EventSource(ServiceReadinessChangedEvent)
    goneaway = EventSource(ServiceGoneAwayEvent)


class ServiceReadinessRequirer(Object):
    """ServiceReadinessRequirer class."""

    on = ServiceReadinessRequirerEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_relation_broken,
        )

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle Service relation changed."""
        logging.debug(f"service readiness data changed for relation {self.relation_name}")
        self.on.readiness_changed.emit(event.relation)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle Service relation broken."""
        logging.debug(f"service readiness relation broken for {self.relation_name}")
        self.on.goneaway.emit(event.relation)

    @property
    def _service_rel(self) -> Relation | None:
        """The service relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> str | None:
        """Return the value for the given key from remote app data."""
        if self._service_rel:
            data = self._service_rel.data[
                self._service_rel.app
            ]
            return data.get(key)

        return None

    @property
    def service_ready(self) -> bool:
        """Return if service is ready or not."""
        is_ready = self.get_remote_app_data("ready")
        if is_ready:
            return json.loads(is_ready)

        return False
