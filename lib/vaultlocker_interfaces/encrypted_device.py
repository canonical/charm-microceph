# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library for the encrypted-device relation."""

import json
from dataclasses import dataclass

from ops.charm import CharmBase, RelationChangedEvent, RelationEvent
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import Relation, Unit

DEVICE_REQUESTS_FIELD = "device_requests"
DEVICE_RESULTS_FIELD = "device_results"


@dataclass
class DeviceRequest:
    """A request to provision a block device.

    Attributes:
        target: Exact device path used as the key in ``device_requests``.
        existing_key_secret_id: ID of a Juju secret containing the current
            LUKS passphrase. If this is None, fresh encryption is requested.
    """

    target: str
    existing_key_secret_id: str | None = None


@dataclass
class DeviceResult:
    """The result of provisioning a block device.

    Attributes:
        target: Exact device path from the request.
        mapper_path: Path to the unlocked device.
        luks_uuid: UUID of the LUKS device.
    """

    target: str
    mapper_path: str
    luks_uuid: str


class DeviceRequestsChangedEvent(RelationEvent):
    """Event emitted when a principal unit's device requests may have changed."""


class DeviceResultsChangedEvent(RelationEvent):
    """Event emitted when a Vaultlocker unit's device results may have changed."""


class EncryptedDeviceRequirerEvents(ObjectEvents):
    """Events emitted by the encrypted-device requirer."""

    requests_changed = EventSource(DeviceRequestsChangedEvent)


class EncryptedDeviceProviderEvents(ObjectEvents):
    """Events emitted by the encrypted-device provider."""

    results_changed = EventSource(DeviceResultsChangedEvent)


class EncryptedDeviceRequires(Object):
    """Requirer side of the encrypted-device relation used by Vaultlocker."""

    on = EncryptedDeviceRequirerEvents()

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)

        relation_events = charm.on[relation_name]
        self.framework.observe(
            relation_events.relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Emit requests_changed for changes made by a principal unit."""
        if event.unit is None:
            return

        self.on.requests_changed.emit(
            event.relation,
            app=event.app,
            unit=event.unit,
        )

    def get_device_requests(
        self,
        relation: Relation,
        unit: Unit,
    ) -> list[DeviceRequest]:
        """Return requests written by a principal unit."""
        raw = relation.data[unit].get(DEVICE_REQUESTS_FIELD)
        if raw is None:
            return []

        return parse_device_requests(raw)

    def set_device_results(
        self,
        relation: Relation,
        results: list[DeviceResult],
    ) -> None:
        """Write device results to the Vaultlocker unit databag."""
        relation.data[self.model.unit][DEVICE_RESULTS_FIELD] = serialize_device_results(results)


class EncryptedDeviceProvides(Object):
    """Provider side of the encrypted-device relation used by principal charms."""

    on = EncryptedDeviceProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        super().__init__(charm, relation_name)

        relation_events = charm.on[relation_name]
        self.framework.observe(
            relation_events.relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_changed(self, event: RelationChangedEvent) -> None:
        """Emit results_changed for changes made by a Vaultlocker unit."""
        if event.unit is None:
            return

        self.on.results_changed.emit(
            event.relation,
            app=event.app,
            unit=event.unit,
        )

    def set_device_requests(
        self,
        relation: Relation,
        requests: list[DeviceRequest],
    ) -> None:
        """Write device requests to the principal unit databag."""
        relation.data[self.model.unit][DEVICE_REQUESTS_FIELD] = serialize_device_requests(requests)

    def get_device_results(
        self,
        relation: Relation,
        unit: Unit,
    ) -> list[DeviceResult]:
        """Return results written by a Vaultlocker unit."""
        raw = relation.data[unit].get(DEVICE_RESULTS_FIELD)
        if raw is None:
            return []

        return parse_device_results(raw)


def _reject_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build a dictionary, rejecting duplicate keys."""
    result: dict[str, object] = {}

    for key, value in pairs:
        if key in result:
            raise ValueError("relation data contains duplicate fields")
        result[key] = value

    return result


def parse_device_requests(raw: str) -> list[DeviceRequest]:
    """Parse device requests from relation data.

    Raises:
        ValueError: If ``device_requests`` contains invalid data.
    """
    try:
        request_map = json.loads(raw, object_pairs_hook=_reject_duplicate_fields)
    except json.JSONDecodeError:
        raise ValueError("device_requests must contain valid JSON")

    if not isinstance(request_map, dict):
        raise ValueError("device_requests must be a JSON object")

    requests: list[DeviceRequest] = []

    for target, request_data in request_map.items():
        if not target.startswith("/"):
            raise ValueError("device request targets must be absolute paths")

        if not isinstance(request_data, dict):
            raise ValueError("device request data must be a JSON object")
        if set(request_data) - {"existing_key_secret_id"}:
            raise ValueError("device request contains unsupported fields")

        secret_id = request_data.get("existing_key_secret_id")
        if "existing_key_secret_id" in request_data and (
            not isinstance(secret_id, str) or not secret_id.strip()
        ):
            raise ValueError("existing_key_secret_id must be a nonempty string")

        requests.append(
            DeviceRequest(
                target=target,
                existing_key_secret_id=secret_id,
            )
        )

    return requests


def serialize_device_requests(requests: list[DeviceRequest]) -> str:
    """Serialize device requests for relation data.

    Raises:
        ValueError: If a request contains invalid data.
    """
    request_map: dict[str, dict[str, str]] = {}

    for request in requests:
        if not isinstance(request.target, str) or not request.target.startswith("/"):
            raise ValueError("device request targets must be absolute paths")
        if request.existing_key_secret_id is not None and (
            not isinstance(request.existing_key_secret_id, str)
            or not request.existing_key_secret_id.strip()
        ):
            raise ValueError("existing_key_secret_id must be a nonempty string")
        if request.target in request_map:
            raise ValueError("device_requests contains duplicate targets")

        request_data: dict[str, str] = {}
        if request.existing_key_secret_id is not None:
            request_data["existing_key_secret_id"] = request.existing_key_secret_id

        request_map[request.target] = request_data

    return json.dumps(request_map, sort_keys=True)


def serialize_device_results(results: list[DeviceResult]) -> str:
    """Serialize device results for relation data.

    Raises:
        ValueError: If a result contains invalid data.
    """
    result_map: dict[str, dict[str, str]] = {}

    for result in results:
        if not isinstance(result.target, str) or not result.target.startswith("/"):
            raise ValueError("device result targets must be absolute paths")
        if not isinstance(result.mapper_path, str) or not result.mapper_path.startswith("/"):
            raise ValueError("mapper_path must be an absolute path")
        if not isinstance(result.luks_uuid, str) or not result.luks_uuid.strip():
            raise ValueError("luks_uuid must be a nonempty string")
        if result.target in result_map:
            raise ValueError("device_results contains duplicate targets")

        result_map[result.target] = {
            "mapper_path": result.mapper_path,
            "luks_uuid": result.luks_uuid,
        }

    return json.dumps(result_map, sort_keys=True)


def parse_device_results(raw: str) -> list[DeviceResult]:
    """Parse device results from relation data.

    Raises:
        ValueError: If ``device_results`` contains invalid data.
    """
    try:
        result_map = json.loads(raw, object_pairs_hook=_reject_duplicate_fields)
    except json.JSONDecodeError:
        raise ValueError("device_results must contain valid JSON")

    if not isinstance(result_map, dict):
        raise ValueError("device_results must be a JSON object")
    results: list[DeviceResult] = []

    for target, result_data in result_map.items():
        if not target.startswith("/"):
            raise ValueError("device result targets must be absolute paths")

        if not isinstance(result_data, dict):
            raise ValueError("device result data must be a JSON object")
        if set(result_data) - {"mapper_path", "luks_uuid"}:
            raise ValueError("device result contains unsupported fields")

        mapper_path = result_data.get("mapper_path")
        if not isinstance(mapper_path, str) or not mapper_path.startswith("/"):
            raise ValueError("mapper_path must be an absolute path")

        luks_uuid = result_data.get("luks_uuid")
        if not isinstance(luks_uuid, str) or not luks_uuid.strip():
            raise ValueError("luks_uuid must be a nonempty string")

        results.append(
            DeviceResult(
                target=target,
                mapper_path=mapper_path,
                luks_uuid=luks_uuid,
            )
        )

    return results
