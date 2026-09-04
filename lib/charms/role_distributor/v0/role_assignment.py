"""Juju charm interface library for the role-assignment relation.

This library provides the ``RoleAssignmentProvider`` and
``RoleAssignmentRequirer`` objects that charm authors use to participate
in the role-assignment relation protocol.

Provider charms use ``RoleAssignmentProvider`` to read unit registrations
and publish per-unit role assignments.

Requirer charms use ``RoleAssignmentRequirer`` to register their units
and receive role assignments from the Provider.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from enum import StrEnum
from typing import Any, Literal

import ops

# The unique Charmhub library identifier, never change it
LIBID = "fceff126a87248c084f21f9fe4630918"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

logger = logging.getLogger(__name__)


class AssignmentStatus(StrEnum):
    """Supported relation assignment states."""

    ASSIGNED = "assigned"
    PENDING = "pending"
    ERROR = "error"

    @classmethod
    def coerce(cls, raw_status: str | AssignmentStatus) -> AssignmentStatus:
        """Convert a wire value to a known status, defaulting to pending."""
        try:
            return cls(raw_status)
        except ValueError:
            return cls.PENDING


@dataclasses.dataclass(frozen=True)
class UnitRoleAssignment:
    """A single unit's role assignment as read from the Provider App databag."""

    status: AssignmentStatus | Literal["assigned", "pending", "error"]
    roles: tuple[str, ...] = ()
    message: str | None = None
    workload_params: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Normalize status inputs to the enum form used by Python callers."""
        object.__setattr__(self, "status", AssignmentStatus.coerce(self.status))

    def to_dict(self) -> dict:
        """Serialize to a dict suitable for JSON encoding.

        Only includes fields relevant to the current status:
        - ``assigned``: ``status`` + ``roles``
        - ``error``: ``status`` + ``message``
        - ``pending``: ``status`` only
        """
        d: dict = {"status": str(self.status)}
        if self.status is AssignmentStatus.ASSIGNED:
            d["roles"] = [*self.roles]
        if self.message is not None and self.status is AssignmentStatus.ERROR:
            d["message"] = self.message
        if self.workload_params is not None and self.status is AssignmentStatus.ASSIGNED:
            d["workload-params"] = self.workload_params
        return d

    @classmethod
    def from_dict(cls, d: dict) -> UnitRoleAssignment:
        """Deserialize from a dict parsed from the Provider App databag.

        Unknown status values are treated as ``pending`` for forward
        compatibility (see spec: Requirer-side validation).
        """
        raw_status = d.get("status", "pending")
        status = AssignmentStatus.coerce(raw_status)
        roles = tuple(d.get("roles", ())) if status is AssignmentStatus.ASSIGNED else ()
        if status is AssignmentStatus.ASSIGNED and not roles:
            logger.warning("Assignment has status 'assigned' but roles list is empty")
        workload_params = d.get("workload-params") if status is AssignmentStatus.ASSIGNED else None
        return cls(
            status=status,
            roles=roles,
            message=d.get("message"),
            workload_params=workload_params,
        )


@dataclasses.dataclass(frozen=True)
class RegisteredUnit:
    """A Requirer unit's registration as read from the relation databags."""

    unit_name: str
    model_name: str
    application_name: str
    machine_id: str | None = None


class RoleAssignmentUnitRegisteredEvent(ops.RelationEvent):
    """Emitted on the Provider when a new Requirer unit registers."""

    def __init__(
        self,
        handle,
        relation,
        unit_name: str,
        model_name: str,
        application_name: str,
        machine_id: str | None,
    ):
        super().__init__(handle, relation)
        self._unit_name = unit_name
        self._model_name = model_name
        self._application_name = application_name
        self._machine_id = machine_id

    @property
    def unit_name(self) -> str:
        return self._unit_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def application_name(self) -> str:
        return self._application_name

    @property
    def machine_id(self) -> str | None:
        return self._machine_id

    def snapshot(self) -> dict:
        d = super().snapshot()
        d["unit_name"] = self._unit_name
        d["model_name"] = self._model_name
        d["application_name"] = self._application_name
        d["machine_id"] = self._machine_id
        return d

    def restore(self, snapshot: dict) -> None:
        super().restore(snapshot)
        self._unit_name = snapshot["unit_name"]
        self._model_name = snapshot["model_name"]
        self._application_name = snapshot["application_name"]
        self._machine_id = snapshot["machine_id"]


class RoleAssignmentUnitDepartedEvent(ops.RelationEvent):
    """Emitted on the Provider when a Requirer unit departs."""

    def __init__(
        self,
        handle,
        relation,
        unit_name: str,
        model_name: str,
        application_name: str,
        machine_id: str | None,
    ):
        super().__init__(handle, relation)
        self._unit_name = unit_name
        self._model_name = model_name
        self._application_name = application_name
        self._machine_id = machine_id

    @property
    def unit_name(self) -> str:
        return self._unit_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def application_name(self) -> str:
        return self._application_name

    @property
    def machine_id(self) -> str | None:
        return self._machine_id

    def snapshot(self) -> dict:
        d = super().snapshot()
        d["unit_name"] = self._unit_name
        d["model_name"] = self._model_name
        d["application_name"] = self._application_name
        d["machine_id"] = self._machine_id
        return d

    def restore(self, snapshot: dict) -> None:
        super().restore(snapshot)
        self._unit_name = snapshot["unit_name"]
        self._model_name = snapshot["model_name"]
        self._application_name = snapshot["application_name"]
        self._machine_id = snapshot["machine_id"]


class RoleAssignmentChangedEvent(ops.RelationEvent):
    """Emitted on the Requirer when this unit's assignment changes."""

    def __init__(
        self,
        handle,
        relation,
        status: AssignmentStatus | str,
        roles: tuple[str, ...],
        message: str | None,
        workload_params: dict[str, Any] | None,
    ):
        super().__init__(handle, relation)
        self._status = AssignmentStatus.coerce(status)
        self._roles = roles
        self._message = message
        self._workload_params = workload_params

    @property
    def status(self) -> AssignmentStatus:
        return self._status

    @property
    def roles(self) -> tuple[str, ...]:
        return self._roles

    @property
    def message(self) -> str | None:
        return self._message

    @property
    def workload_params(self) -> dict[str, Any] | None:
        return self._workload_params

    def snapshot(self) -> dict:
        d = super().snapshot()
        d["status"] = str(self._status)
        d["roles"] = list(self._roles)
        d["message"] = self._message
        d["workload_params"] = self._workload_params
        return d

    def restore(self, snapshot: dict) -> None:
        super().restore(snapshot)
        self._status = AssignmentStatus.coerce(snapshot["status"])
        self._roles = tuple(snapshot["roles"])
        self._message = snapshot["message"]
        self._workload_params = snapshot["workload_params"]


class RoleAssignmentRevokedEvent(ops.RelationEvent):
    """Emitted on the Requirer when the relation breaks or the entry disappears."""


class RoleAssignmentProviderEvents(ops.ObjectEvents):
    """Events emitted by RoleAssignmentProvider."""

    unit_registered = ops.EventSource(RoleAssignmentUnitRegisteredEvent)
    unit_departed = ops.EventSource(RoleAssignmentUnitDepartedEvent)


class RoleAssignmentRequirerEvents(ops.ObjectEvents):
    """Events emitted by RoleAssignmentRequirer."""

    role_assignment_changed = ops.EventSource(RoleAssignmentChangedEvent)
    role_assignment_revoked = ops.EventSource(RoleAssignmentRevokedEvent)


class RoleAssignmentRequirer(ops.Object):
    """Requirer side of the role-assignment interface.

    Handles unit registration (writing identity to databags) and reading
    role assignments from the Provider App databag.

    This library is stateless — it does not use ``StoredState``. Every
    ``relation-changed`` event that carries a valid assignment emits
    ``role_assignment_changed``. Charms are responsible for their own
    idempotency if they need to avoid redundant reconfiguration.
    """

    on = RoleAssignmentRequirerEvents()

    def __init__(self, charm: ops.CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self.framework.observe(
            charm.on[relation_name].relation_joined,
            self._on_relation_joined,
        )
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[relation_name].relation_broken,
            self._on_relation_broken,
        )
        self.framework.observe(
            charm.on.leader_elected,
            self._on_leader_elected,
        )

    def _relation(self) -> ops.Relation | None:
        return self.model.get_relation(self._relation_name)

    def _on_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        event.relation.data[self._charm.unit]["unit-name"] = self._charm.unit.name
        machine_id = self._resolve_machine_id()
        if machine_id is not None:
            event.relation.data[self._charm.unit]["machine-id"] = machine_id
        if self._charm.unit.is_leader():
            event.relation.data[self._charm.app]["model-name"] = self.model.name
            event.relation.data[self._charm.app]["application-name"] = self._charm.app.name

    @staticmethod
    def _resolve_machine_id() -> str | None:
        """Resolve the Juju machine ID if available.

        Tries ``ops.JujuContext`` (ops >= 3.5.1) first, then falls back
        to the ``JUJU_MACHINE_ID`` environment variable. Returns ``None``
        on Kubernetes or when the machine ID cannot be determined.
        """
        if hasattr(ops, "JujuContext"):
            ctx = ops.JujuContext.from_environ()
            if ctx.machine_id is not None:
                return ctx.machine_id
        return os.environ.get("JUJU_MACHINE_ID")

    def _on_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        assignment = self._read_assignment(event.relation)
        if assignment is None:
            return
        self.on.role_assignment_changed.emit(
            event.relation,
            assignment.status,
            assignment.roles,
            assignment.message,
            assignment.workload_params,
        )

    def _on_relation_broken(self, event: ops.RelationBrokenEvent) -> None:
        self.on.role_assignment_revoked.emit(event.relation)

    def _on_leader_elected(self, event: ops.LeaderElectedEvent) -> None:
        relation = self._relation()
        if relation is not None:
            relation.data[self._charm.app]["model-name"] = self.model.name
            relation.data[self._charm.app]["application-name"] = self._charm.app.name

    def _read_assignment(self, relation: ops.Relation) -> UnitRoleAssignment | None:
        remote_app = relation.app
        if remote_app is None:
            return None
        raw = relation.data[remote_app].get("assignments")
        if raw is None:
            return None
        try:
            assignments_map = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Malformed assignments JSON in Provider App databag")
            return None
        unit_entry = assignments_map.get(self._charm.unit.name)
        if unit_entry is None:
            return None
        return UnitRoleAssignment.from_dict(unit_entry)

    def get_assignment(self) -> UnitRoleAssignment | None:
        """Read this unit's assignment from the Provider App databag.

        Returns ``None`` if no relation exists, the Provider hasn't
        published assignments, or there is no entry for this unit.
        """
        relation = self._relation()
        if relation is None:
            return None
        return self._read_assignment(relation)


class RoleAssignmentProvider(ops.Object):
    """Provider side of the role-assignment interface.

    Reads Requirer unit registrations, publishes role assignments, and
    emits semantic events when units register or depart.

    This library is stateless — it does not use ``StoredState``. Every
    ``relation-changed`` event emits ``unit_registered`` for each unit
    currently present on the relation. The charm is responsible for its
    own idempotency and cross-relation reconciliation (e.g. calling
    ``get_all_registered_units()`` and re-publishing assignments on all
    relations when the topology changes).
    """

    on = RoleAssignmentProviderEvents()

    def __init__(self, charm: ops.CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[relation_name].relation_departed,
            self._on_relation_departed,
        )
        self.framework.observe(
            charm.on.leader_elected,
            self._on_leader_elected,
        )

    def _on_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        model_name = self._read_model_name(event.relation) or ""
        application_name = self._read_application_name(event.relation) or ""
        for unit in event.relation.units:
            unit_name = event.relation.data[unit].get("unit-name")
            if not unit_name:
                continue
            machine_id = event.relation.data[unit].get("machine-id")
            self.on.unit_registered.emit(
                event.relation,
                unit_name,
                model_name,
                application_name,
                machine_id,
            )

    def _on_relation_departed(self, event: ops.RelationDepartedEvent) -> None:
        departing = event.departing_unit
        if departing is None:
            return
        unit_name = event.relation.data[departing].get("unit-name")
        if unit_name is None:
            return
        model_name = self._read_model_name(event.relation) or ""
        application_name = self._read_application_name(event.relation) or ""
        machine_id = event.relation.data[departing].get("machine-id")
        self.on.unit_departed.emit(
            event.relation, unit_name, model_name, application_name, machine_id
        )

    def _on_leader_elected(self, event: ops.LeaderElectedEvent) -> None:
        """Re-emit unit_registered for all units on all relations."""
        for rel in self._charm.model.relations.get(self._relation_name, []):
            model_name = self._read_model_name(rel) or ""
            application_name = self._read_application_name(rel) or ""
            for unit in rel.units:
                unit_name = rel.data[unit].get("unit-name")
                if not unit_name:
                    continue
                machine_id = rel.data[unit].get("machine-id")
                self.on.unit_registered.emit(
                    rel, unit_name, model_name, application_name, machine_id
                )

    def _read_model_name(self, relation: ops.Relation) -> str | None:
        remote_app = relation.app
        if remote_app is None:
            return None
        return relation.data[remote_app].get("model-name")

    def _read_application_name(self, relation: ops.Relation) -> str | None:
        remote_app = relation.app
        if remote_app is None:
            return None
        return relation.data[remote_app].get("application-name")

    def get_registered_units(self, relation: ops.Relation) -> list[RegisteredUnit]:
        """Read all Requirer unit registrations from a single relation."""
        model_name = self._read_model_name(relation) or ""
        application_name = self._read_application_name(relation) or ""
        result = []
        for unit in relation.units:
            unit_name = relation.data[unit].get("unit-name")
            if not unit_name:
                continue
            machine_id = relation.data[unit].get("machine-id")
            result.append(
                RegisteredUnit(
                    unit_name=unit_name,
                    model_name=model_name,
                    application_name=application_name,
                    machine_id=machine_id,
                )
            )
        return result

    def get_all_registered_units(self) -> list[RegisteredUnit]:
        """Read all Requirer unit registrations across all relations."""
        result = []
        for rel in self._charm.model.relations.get(self._relation_name, []):
            result.extend(self.get_registered_units(rel))
        return result

    def set_assignments(
        self,
        relation: ops.Relation,
        assignments: dict[str, UnitRoleAssignment],
    ) -> None:
        """Write the full assignment map to the Provider App databag."""
        data = {unit_name: assignment.to_dict() for unit_name, assignment in assignments.items()}
        relation.data[self._charm.app]["assignments"] = json.dumps(data)
