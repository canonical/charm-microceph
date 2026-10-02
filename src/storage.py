#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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

"""Handle Charm's Storage Events."""

import json
import logging
from dataclasses import asdict
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired, run
from types import SimpleNamespace

import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.guard as sunbeam_guard
from ops.charm import ActionEvent, CharmBase, StorageAttachedEvent, StorageDetachingEvent
from ops.framework import Object, StoredState
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from tenacity import retry, stop_after_attempt, wait_fixed

import microceph
import utils
from device_flags import DeviceAddFlags, parse_device_add_flags
from encrypted_device import (
    DEVICE_REQUESTS_KEY,
    DEVICE_RESULTS_KEY,
    build_fresh_device_requests,
    parse_device_results,
    render_osd_unlock_dropin,
    resolve_stable_block_device,
    validate_fresh_encryption_target,
    validate_mapper_block_device,
)

logger = logging.getLogger(__name__)


class StorageHandler(Object):
    """The Storage class manages the storage events.

    Observes the following events:
    1) *_storage_attached
    2) *_storage_detaching
    3) add_osd_action
    4) list_disks_action
    5) config_changed (for osd-devices processing)
    """

    name = "storage"

    # storage directive names
    standalone = "osd-standalone"
    encrypted_device_relation = "encrypted-device"
    vaultlocker_provider = "vaultlocker"
    vaultlocker_action_prefix = "add-osd:"
    vaultlocker_osd_dropin = Path(
        "/etc/systemd/system/snap.microceph.osd.service.d/vaultlocker.conf"
    )

    charm = None
    # _stored: per unit stored state for storage class. Contains:
    #  osd_data: dict of dicts with int (osd num) key
    #    disk: OSD disk storage name (unique)
    _stored = StoredState()

    def __init__(self, charm: CharmBase, name="storage"):
        super().__init__(charm, name)
        self._stored.set_default(
            osd_data={},
            last_osd_devices="",
            last_wipe_osd=False,
            last_encrypt_osd=False,
            last_storage_config_signature="",
            vaultlocker_devices={},
            vaultlocker_boot_order_dirty=False,
        )
        self.charm = charm
        self.name = name
        self.storage_status = compound_status.Status(self.name)
        self.storage_config_status = compound_status.Status(f"{self.name}-config")
        self.charm.status_pool.add(self.storage_status)
        self.charm.status_pool.add(self.storage_config_status)
        self._storage_guard = SimpleNamespace(status=self.storage_status)
        self._storage_config_guard = SimpleNamespace(status=self.storage_config_status)

        # Attach handlers
        self.framework.observe(
            charm.on[self.standalone.replace("-", "_")].storage_attached,
            self._on_osd_standalone_attached,
        )

        # OSD Detaching handlers.
        self.framework.observe(
            charm.on[self.standalone.replace("-", "_")].storage_detaching,
            self._on_storage_detaching,
        )
        self.framework.observe(
            charm.on[self.encrypted_device_relation].relation_joined,
            self._on_encrypted_device_relation_changed,
        )
        self.framework.observe(
            charm.on[self.encrypted_device_relation].relation_changed,
            self._on_encrypted_device_relation_changed,
        )
        self.framework.observe(
            charm.on[self.encrypted_device_relation].relation_broken,
            self._on_encrypted_device_relation_broken,
        )

        self.framework.observe(charm.on.add_osd_action, self._add_osd_action)
        self.framework.observe(charm.on.list_disks_action, self._list_disks_action)

        # Observe config-changed for osd-devices processing
        self.framework.observe(charm.on.config_changed, self._on_config_changed_osd_devices)

    # storage event handlers

    def _on_osd_standalone_attached(self, event: StorageAttachedEvent):
        """Storage attached handler for osd-standalone."""
        if not self.charm.ready_for_service():
            logger.warning("MicroCeph not ready yet, deferring storage event.")
            event.defer()
            return

        try:
            vaultlocker_mode = self._vaultlocker_mode_enabled()
        except sunbeam_guard.BlockedExceptionError as exc:
            self.storage_status.set(exc.to_status())
            return

        if vaultlocker_mode:
            with sunbeam_guard.guard(self._storage_guard, self.name):
                self._reconcile_vaultlocker_osds(event)
            return

        self._clean_stale_osd_data()

        enroll = []

        logger.debug(f"storage on unit: {self._fetch_filtered_storages([self.standalone])}")

        for storage in self._fetch_filtered_storages([self.standalone]):
            logger.debug(f"Processing {storage}")
            if not self._get_osd_id(name=storage):
                enroll.append(storage)

        logger.debug(f"Enroll list {enroll}")
        with sunbeam_guard.guard(self._storage_guard, self.name):
            self.storage_status.set(MaintenanceStatus("Enrolling OSDs"))
            self._enroll_disks_in_batch(enroll)
            self.storage_status.set(ActiveStatus(""))
            self._restore_ready_workload_status()

    def _osd_encryption_provider(self) -> str:
        """Return the configured provider after rejecting unsafe unknown values."""
        provider = self.charm.model.config.get("osd-encryption-provider", "none")
        if provider not in {"none", self.vaultlocker_provider}:
            raise sunbeam_guard.BlockedExceptionError(
                f"Invalid osd-encryption-provider: {provider}"
            )
        if provider == "none" and self._stored.vaultlocker_devices:
            raise sunbeam_guard.BlockedExceptionError(
                "Cannot disable vaultlocker OSD encryption while "
                "Vaultlocker-managed OSD storage exists"
            )
        return provider

    def _vaultlocker_mode_enabled(self) -> bool:
        """Whether new standalone OSD storage is delegated to Vaultlocker."""
        return self._osd_encryption_provider() == self.vaultlocker_provider

    def _on_encrypted_device_relation_changed(self, event) -> None:
        """Reconcile pending Vaultlocker-backed OSD storage on relation changes."""
        with sunbeam_guard.guard(self._storage_guard, self.name):
            if self._vaultlocker_mode_enabled():
                self._reconcile_vaultlocker_osds(event)

    def _on_encrypted_device_relation_broken(self, _event) -> None:
        """Report that pending Vaultlocker requests can no longer complete."""
        with sunbeam_guard.guard(self._storage_guard, self.name):
            if not self._vaultlocker_mode_enabled():
                return

            if self._stored.vaultlocker_devices:
                self.storage_status.set(
                    BlockedStatus(
                        "Vaultlocker relation removed while encrypted OSD storage is managed"
                    )
                )

    def _reconcile_vaultlocker_osds(self, event) -> None:
        """Publish fresh-encryption requests for attached standalone OSD storage."""
        if not self.charm.ready_for_service():
            logger.warning("MicroCeph not ready yet, deferring Vaultlocker storage reconciliation")
            event.defer()
            return

        relation = self.charm.model.get_relation(self.encrypted_device_relation)
        if relation is None:
            self.storage_status.set(
                BlockedStatus("Vaultlocker relation is required for vaultlocker OSD encryption")
            )
            return

        self._clean_stale_osd_data()
        self._assert_vaultlocker_relation_is_unchanged(relation)
        requested_paths, pending_paths = self._reconcile_vaultlocker_storage_requests(
            relation,
            self._vaultlocker_results(relation),
        )
        self._publish_vaultlocker_requests(relation)
        self._update_vaultlocker_boot_order_if_needed()
        self._set_vaultlocker_reconcile_status(requested_paths, pending_paths)

    def _assert_vaultlocker_relation_is_unchanged(self, relation) -> None:
        """Ensure durable requests are never transferred to a new provider relation."""
        if any(
            request.get("relation_id") != relation.id
            for request in self._stored.vaultlocker_devices.values()
        ):
            raise sunbeam_guard.BlockedExceptionError(
                "Vaultlocker relation changed while OSD storage is managed"
            )

    def _reconcile_vaultlocker_storage_requests(
        self, relation, results: dict
    ) -> tuple[list, list]:
        """Reconcile attached storage and pending add-osd action requests."""
        requested_paths = []
        pending_paths = []
        for storage_name in self._fetch_filtered_storages([self.standalone]):
            request_path, pending = self._reconcile_vaultlocker_storage(
                storage_name,
                relation,
                results,
            )
            if request_path is not None:
                requested_paths.append(request_path)
            if pending:
                pending_paths.append(request_path)

        action_requested, action_pending = self._reconcile_vaultlocker_action_requests(results)
        requested_paths.extend(action_requested)
        pending_paths.extend(action_pending)
        return requested_paths, pending_paths

    def _reconcile_vaultlocker_action_requests(self, results: dict) -> tuple[list, list]:
        """Consume results for direct-device add-osd requests without storage events."""
        requested_paths = []
        pending_paths = []
        for request_key, request in self._stored.vaultlocker_devices.items():
            if request.get("source") != "add-osd":
                continue

            request_path = request["request_path"]
            result = results.get(request_path)
            if request.get("phase") == "enrolled":
                if result is not None:
                    self._assert_vaultlocker_result_is_unchanged(request, result, completed=True)
                continue
            if result is None:
                requested_paths.append(request_path)
                pending_paths.append(request_path)
                continue

            self._consume_vaultlocker_result(request_key, request, result)
            requested_paths.append(request_path)
        return requested_paths, pending_paths

    def _reconcile_vaultlocker_storage(
        self,
        storage_name: str,
        relation,
        results: dict,
    ) -> tuple[str | None, bool]:
        """Reconcile one standalone storage attachment with its Vaultlocker result."""
        request = self._stored.vaultlocker_devices.get(storage_name)
        if self._get_osd_id(storage_name) is not None:
            self._assert_completed_vaultlocker_result_is_unchanged(request, results)
            return None, False

        request = self._vaultlocker_request_for_storage(storage_name, relation)
        request_path = request["request_path"]
        result = results.get(request_path)
        if result is None:
            return request_path, True

        self._consume_vaultlocker_result(storage_name, request, result)
        return request_path, False

    def _assert_completed_vaultlocker_result_is_unchanged(
        self,
        request: dict | None,
        results: dict,
    ) -> None:
        """Verify that a mapped OSD still has the result that completed its request."""
        if request is None or request.get("phase") != "enrolled":
            return
        result = results.get(request["request_path"])
        if result is not None:
            self._assert_vaultlocker_result_is_unchanged(request, result, completed=True)

    def _vaultlocker_request_for_storage(self, storage_name: str, relation) -> dict:
        """Return the immutable request state for an attached storage device."""
        attachment_path = self.juju_storage_get(storage_id=storage_name, attribute="location")
        stable_device = self._resolve_vaultlocker_stable_device(attachment_path)
        request = self._stored.vaultlocker_devices.get(storage_name)
        if request is not None:
            self._assert_vaultlocker_attachment_is_unchanged(request, stable_device)
            return request

        self._validate_vaultlocker_fresh_target(stable_device.path)
        self._assert_vaultlocker_device_is_unique(storage_name, stable_device.rdev)
        request = {
            "request_path": stable_device.path,
            "rdev": stable_device.rdev,
            "relation_id": relation.id,
            "phase": "requested",
        }
        self._stored.vaultlocker_devices[storage_name] = request
        return request

    def _resolve_vaultlocker_stable_device(self, attachment_path: str):
        """Resolve a Juju attachment to the stable path required by the relation contract."""
        try:
            return resolve_stable_block_device(attachment_path)
        except (OSError, ValueError) as exc:
            logger.warning("Could not resolve a stable OSD storage identity: %s", exc)
            raise sunbeam_guard.BlockedExceptionError(
                "OSD storage does not have a stable /dev/disk/by-id identity"
            ) from exc

    def _validate_vaultlocker_fresh_target(self, device_path: str) -> None:
        """Convert local preflight failures into an operator-facing status."""
        try:
            validate_fresh_encryption_target(device_path)
        except ValueError as exc:
            logger.warning("OSD storage is not safe for Vaultlocker encryption: %s", exc)
            raise sunbeam_guard.BlockedExceptionError(
                "OSD storage is not safe for Vaultlocker encryption"
            ) from exc

    def _assert_vaultlocker_device_is_unique(self, storage_name: str, rdev: int) -> None:
        """Reject aliases of a device already tracked by another Vaultlocker request."""
        if any(
            other_storage != storage_name and other_request.get("rdev") == rdev
            for other_storage, other_request in self._stored.vaultlocker_devices.items()
        ):
            raise sunbeam_guard.BlockedExceptionError(
                "The same block device is already requested through Vaultlocker"
            )

    def _assert_vaultlocker_attachment_is_unchanged(self, request: dict, stable_device) -> None:
        """Ensure Juju has not replaced a device while it is being provisioned."""
        if request["rdev"] != stable_device.rdev or request["request_path"] != stable_device.path:
            raise sunbeam_guard.BlockedExceptionError(
                "Juju storage attachment changed during Vaultlocker provisioning"
            )

    def _consume_vaultlocker_result(self, storage_name: str, request: dict, result) -> None:
        """Enroll the returned mapper once and recover its OSD mapping after a retry."""
        self._validate_vaultlocker_mapper(result.mapper_path)
        phase = request.get("phase")
        if phase == "enrolled":
            self._assert_vaultlocker_result_is_unchanged(request, result, completed=True)
            return
        if phase == "enrolling":
            self._assert_vaultlocker_result_is_unchanged(request, result, completed=False)
            self._save_vaultlocker_osd_data(storage_name, result.mapper_path)
            request["phase"] = "enrolled"
            return

        self._enroll_vaultlocker_mapper(storage_name, request, result)

    def _validate_vaultlocker_mapper(self, mapper_path: str) -> None:
        """Convert an unavailable provider mapper into an operator-facing status."""
        try:
            validate_mapper_block_device(mapper_path)
        except ValueError as exc:
            logger.warning("Vaultlocker mapper is not usable: %s", exc)
            raise sunbeam_guard.BlockedExceptionError(
                "Vaultlocker returned an unavailable mapper device"
            ) from exc

    def _assert_vaultlocker_result_is_unchanged(
        self,
        request: dict,
        result,
        *,
        completed: bool,
    ) -> None:
        """Reject a provider result that changes after MicroCeph starts consuming it."""
        if (
            request.get("mapper_path") == result.mapper_path
            and request.get("luks_uuid") == result.luks_uuid
        ):
            return
        if completed:
            raise sunbeam_guard.BlockedExceptionError(
                "Vaultlocker changed a completed device result"
            )
        raise sunbeam_guard.BlockedExceptionError(
            "Vaultlocker changed a result after OSD enrollment started"
        )

    def _enroll_vaultlocker_mapper(self, storage_name: str, request: dict, result) -> None:
        """Prepare snap access and enroll the mapper as a MicroCeph OSD."""
        microceph.ensure_dm_crypt()
        request.update(
            {
                "mapper_path": result.mapper_path,
                "luks_uuid": result.luks_uuid,
                "phase": "enrolling",
            }
        )
        microceph.enroll_disks_as_osds([result.mapper_path])
        self._save_vaultlocker_osd_data(storage_name, result.mapper_path)
        request["phase"] = "enrolled"

    def _update_vaultlocker_boot_order_if_needed(self) -> None:
        """Synchronize OSD boot ordering whenever it changed or an OSD is managed."""
        has_enrolled_mapper = any(
            request.get("phase") == "enrolled"
            for request in self._stored.vaultlocker_devices.values()
        )
        if has_enrolled_mapper or self._stored.vaultlocker_boot_order_dirty:
            self._update_vaultlocker_boot_order()

    def _set_vaultlocker_reconcile_status(
        self,
        requested_paths: list,
        pending_paths: list,
    ) -> None:
        """Set a storage status that reflects relation request completion."""
        if not requested_paths:
            self.storage_status.set(ActiveStatus(""))
            return
        if pending_paths:
            self.storage_status.set(
                WaitingStatus("Waiting for Vaultlocker to prepare OSD storage")
            )
            return
        self.storage_status.set(ActiveStatus(""))
        self._restore_ready_workload_status()

    def _publish_vaultlocker_requests(self, relation) -> None:
        """Publish the complete immutable request map from local durable state."""
        device_paths = [
            request["request_path"] for request in self._stored.vaultlocker_devices.values()
        ]
        device_requests = build_fresh_device_requests(device_paths)
        relation_data = relation.data[self.charm.unit]
        if relation_data.get(DEVICE_REQUESTS_KEY) != device_requests:
            relation_data[DEVICE_REQUESTS_KEY] = device_requests

    def _withdraw_vaultlocker_request(self, storage_name: str) -> None:
        """Withdraw a request after its associated Juju storage is detached."""
        request = self._stored.vaultlocker_devices.pop(storage_name, None)
        relation = self.charm.model.get_relation(self.encrypted_device_relation)
        if relation is not None:
            self._publish_vaultlocker_requests(relation)
        if request and request.get("phase") == "enrolled":
            self._update_vaultlocker_boot_order()

    def _update_vaultlocker_boot_order(self) -> None:
        """Order the MicroCeph OSD service after every managed unlock unit."""
        luks_uuids = sorted(
            {
                request["luks_uuid"]
                for request in self._stored.vaultlocker_devices.values()
                if request.get("phase") == "enrolled" and request.get("luks_uuid")
            }
        )
        dropin = self.vaultlocker_osd_dropin
        if luks_uuids:
            content = render_osd_unlock_dropin(luks_uuids)
            existing_content = dropin.read_text(encoding="utf-8") if dropin.exists() else None
            if existing_content != content:
                self._stored.vaultlocker_boot_order_dirty = True
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin.write_text(content, encoding="utf-8")
                dropin.chmod(0o644)
        elif dropin.exists():
            self._stored.vaultlocker_boot_order_dirty = True
            dropin.unlink()

        if not self._stored.vaultlocker_boot_order_dirty:
            return
        self._run(["systemctl", "daemon-reload"])
        self._stored.vaultlocker_boot_order_dirty = False

    def _vaultlocker_results(self, relation) -> dict:
        """Return the complete, non-conflicting result map from provider units."""
        results = {}
        for unit in relation.units:
            raw_results = relation.data[unit].get(DEVICE_RESULTS_KEY)
            if not raw_results:
                continue
            try:
                unit_results = parse_device_results(raw_results)
            except ValueError as exc:
                logger.warning("Vaultlocker returned an invalid device result: %s", exc)
                raise sunbeam_guard.BlockedExceptionError(
                    "Invalid Vaultlocker device result"
                ) from exc
            for path, result in unit_results.items():
                existing = results.get(path)
                if existing is not None and existing != result:
                    raise sunbeam_guard.BlockedExceptionError(
                        "Vaultlocker returned conflicting results for an OSD device"
                    )
                results[path] = result
        return results

    def _on_storage_detaching(self, event: StorageDetachingEvent):
        """Unified storage detaching handler."""
        # check if the detaching device (of the form directive/index)
        # is being used as or with an OSD.
        logger.debug(f"Detach event received for : {event.storage.full_id}")
        storage_name = event.storage.full_id
        osd_num = self._get_osd_id(storage_name)

        logger.debug(f"OSD ID for: {storage_name} is {osd_num}")
        request = self._stored.vaultlocker_devices.get(storage_name)
        if osd_num is None and request and request.get("mapper_path"):
            osd_num = self._get_vaultlocker_mapper_osd_id(request["mapper_path"])
            if osd_num is not None:
                self._stored.osd_data[osd_num] = {"disk": storage_name}

        if osd_num is None:
            if request:
                self._withdraw_vaultlocker_request(storage_name)
            elif self._stored.vaultlocker_boot_order_dirty:
                self._update_vaultlocker_boot_order()
            return

        # Whole-application teardown: when the entire application is being removed
        # the cluster is being destroyed, so removing OSDs from it is pointless and
        # will hang once the cluster drops below quorum (the disk operations are
        # dqlite-backed). Skip and let Juju deprovision the storage.
        if utils.is_departing(self.charm.app, context="storage detach"):
            logger.info("Application is being removed; skipping OSD removal for osd.%s", osd_num)
            return

        with sunbeam_guard.guard(self._storage_guard, self.name):
            try:
                self.remove_osd(osd_num)
                self._withdraw_vaultlocker_request(storage_name)
                self._restore_ready_workload_status()
            except CalledProcessError as e:
                err_msg = self._error_message(e)
                if self._is_safety_failure(err_msg):
                    warning = (
                        f"Storage {event.storage.full_id} detached, provide replacement "
                        f"for osd.{osd_num}."
                    )
                    logger.warning(warning)
                    # Forcefully remove the OSD because Juju WILL deprovision storage.
                    # Its Vaultlocker request and boot dependency must be removed as well.
                    self.remove_osd(osd_num, force=True)
                    self._withdraw_vaultlocker_request(storage_name)
                    raise sunbeam_guard.BlockedExceptionError(warning)

    def _restore_ready_workload_status(self) -> None:
        """Restore the ready message without clearing non-idle workload states."""
        workload_status = self.charm.status.status
        current_message = getattr(workload_status, "message", "")

        if workload_status.name == "unknown":
            self.charm.status.set(ActiveStatus("charm is ready"))
            return

        if workload_status.name != "active":
            logger.debug(
                "Skipping ready workload status restore because workload slot is %s: %s",
                workload_status.name,
                current_message,
            )
            return

        if current_message and current_message != "charm is ready":
            logger.debug(
                "Skipping ready workload status restore because workload slot already has "
                "an active message: %s",
                current_message,
            )
            return

        self.charm.status.set(ActiveStatus("charm is ready"))

    def _handle_vaultlocker_add_osd_action(self, event: ActionEvent) -> bool:
        """Publish an asynchronous fresh-encryption request for an add-osd action."""
        try:
            vaultlocker_mode = self._vaultlocker_mode_enabled()
        except sunbeam_guard.BlockedExceptionError as exc:
            return self._fail_vaultlocker_add_osd_action(event, exc.msg)

        if not vaultlocker_mode:
            return False

        try:
            device_ids = self._vaultlocker_add_osd_action_device_ids(event)
        except sunbeam_guard.BlockedExceptionError as exc:
            return self._fail_vaultlocker_add_osd_action(event, exc.msg)

        relation = self.charm.model.get_relation(self.encrypted_device_relation)
        if relation is None:
            return self._fail_vaultlocker_add_osd_action(
                event,
                "Vaultlocker relation is required for vaultlocker OSD encryption",
            )

        existing_request_keys = set(self._stored.vaultlocker_devices)
        try:
            self._assert_vaultlocker_relation_is_unchanged(relation)
            requests = self._vaultlocker_action_requests_for_devices(device_ids, relation)
        except sunbeam_guard.BlockedExceptionError as exc:
            for request_key in set(self._stored.vaultlocker_devices) - existing_request_keys:
                self._stored.vaultlocker_devices.pop(request_key)
            return self._fail_vaultlocker_add_osd_action(event, exc.msg)

        self._publish_vaultlocker_requests(relation)
        request_paths = [request["request_path"] for request in requests]
        pending_paths = [
            request["request_path"] for request in requests if request.get("phase") != "enrolled"
        ]
        self._set_vaultlocker_reconcile_status(request_paths, pending_paths)
        event.set_results(
            {
                "result": [
                    {
                        "request-path": request["request_path"],
                        "spec": device_id,
                        "status": "enrolled" if request.get("phase") == "enrolled" else "pending",
                    }
                    for device_id, request in zip(device_ids, requests)
                ]
            }
        )
        return True

    def _fail_vaultlocker_add_osd_action(self, event: ActionEvent, message: str) -> bool:
        """Report a Vaultlocker action validation error as an action failure."""
        event.set_results({"message": message})
        event.fail(message)
        return True

    def _vaultlocker_add_osd_action_device_ids(self, event: ActionEvent) -> list[str]:
        """Validate action-only constraints and return direct device IDs."""
        if not event.params.get("encrypt", False):
            raise sunbeam_guard.BlockedExceptionError(
                "add-osd requires encrypt=true with vaultlocker OSD encryption"
            )
        if event.params.get("loop-spec") is not None:
            raise sunbeam_guard.BlockedExceptionError(
                "loop-spec is not supported with vaultlocker OSD encryption"
            )
        if event.params.get("wipe", False):
            raise sunbeam_guard.BlockedExceptionError(
                "wipe is not supported with vaultlocker OSD encryption"
            )
        device_ids = [
            device_id.strip()
            for device_id in (event.params.get("device-id") or "").split(",")
            if device_id.strip()
        ]
        if not device_ids:
            raise sunbeam_guard.BlockedExceptionError(
                "device-id is required with vaultlocker OSD encryption"
            )
        return device_ids

    def _vaultlocker_action_requests_for_devices(
        self, device_ids: list[str], relation
    ) -> list[dict]:
        """Create action requests while rejecting aliases repeated in the same action."""
        requests = []
        request_paths = set()
        for device_id in device_ids:
            request = self._vaultlocker_action_request_for_device(device_id, relation)
            request_path = request["request_path"]
            if request_path in request_paths:
                raise sunbeam_guard.BlockedExceptionError(
                    "add-osd includes the same block device more than once"
                )
            request_paths.add(request_path)
            requests.append(request)
        return requests

    def _vaultlocker_action_request_for_device(self, device_id: str, relation) -> dict:
        """Create or return the immutable Vaultlocker request for an action device."""
        stable_device = self._resolve_vaultlocker_stable_device(device_id)
        request_key = f"{self.vaultlocker_action_prefix}{stable_device.path}"
        request = self._stored.vaultlocker_devices.get(request_key)
        if request is not None:
            if (
                request["rdev"] != stable_device.rdev
                or request["request_path"] != stable_device.path
            ):
                raise sunbeam_guard.BlockedExceptionError(
                    "add-osd device changed during Vaultlocker provisioning"
                )
            return request

        self._validate_vaultlocker_fresh_target(stable_device.path)
        self._assert_vaultlocker_device_is_unique(request_key, stable_device.rdev)
        request = {
            "request_path": stable_device.path,
            "rdev": stable_device.rdev,
            "relation_id": relation.id,
            "phase": "requested",
            "source": "add-osd",
        }
        self._stored.vaultlocker_devices[request_key] = request
        return request

    def _add_osd_action(self, event: ActionEvent):
        """Add OSD disks to microceph."""
        if not self.charm.peers.interface.state.joined:
            event.set_results({"message": "Node not yet joined in microceph cluster"})
            event.fail()
            return

        if self._handle_vaultlocker_add_osd_action(event):
            return

        # list of osd specs to be executed with disk add cmd.
        add_osd_specs = list()

        # fetch requested loop spec.
        loop_spec = event.params.get("loop-spec", None)
        if loop_spec is not None:
            add_osd_specs.append(f"loop,{loop_spec}")

        # fetch requested disks.
        device_ids = event.params.get("device-id")
        if device_ids is not None:
            add_osd_specs.extend(device_ids.split(","))

        # fetch requested wipe flag.
        wipe = event.params.get("wipe", False)
        encrypt = event.params.get("encrypt", False)

        error = False
        result = {"result": []}
        for spec in add_osd_specs:
            try:
                microceph.add_osd_cmd(spec, wipe=wipe, encrypt=encrypt)
                result["result"].append({"spec": spec, "status": "success"})
            except (CalledProcessError, TimeoutExpired, ValueError) as e:
                err_msg = self._error_message(e)
                logger.error(
                    "Failed add-osd for spec=%s wipe=%s encrypt=%s: %s",
                    spec,
                    wipe,
                    encrypt,
                    err_msg,
                )
                result["result"].append({"spec": spec, "status": "failure", "message": err_msg})
                error = True

        event.set_results(result)
        if error:
            event.fail()

    def _list_disks_action(self, event: ActionEvent):
        """List enrolled and unconfigured disks."""
        if not self.charm.peers.interface.state.joined:
            event.set_results({"message": "Node not yet joined in microceph cluster"})
            event.fail()
            return

        host_only = event.params.get("host-only", False)
        try:
            disks = microceph.list_disk_cmd(host_only=host_only)
        except (CalledProcessError, TimeoutExpired) as e:
            err_msg = self._error_message(e)
            logger.warning("Failed list-disks host_only=%s: %s", host_only, err_msg)
            event.set_results({"message": err_msg})
            event.fail()
            return

        osds = [self._to_lower_dict(osd) for osd in disks["ConfiguredDisks"]]
        available_disks = [self._to_lower_dict(disk) for disk in disks["AvailableDisks"]]

        # result should conform to previous expectations.
        event.set_results({"osds": osds, "unpartitioned-disks": available_disks})

    def _on_config_changed_osd_devices(self, event):
        """Process config-driven storage requests for OSD/WAL/DB matching."""
        with sunbeam_guard.guard(self._storage_config_guard, f"{self.name}-config"):
            encryption_provider = self._osd_encryption_provider()
            if encryption_provider == self.vaultlocker_provider:
                if self._normalized_config_value("osd-devices"):
                    raise sunbeam_guard.BlockedExceptionError(
                        "osd-devices is not supported with vaultlocker OSD encryption"
                    )
                if self._normalized_config_value("device-add-flags"):
                    raise sunbeam_guard.BlockedExceptionError(
                        "device-add-flags is not supported with vaultlocker OSD encryption"
                    )
                if self._has_ignored_waldb_config():
                    raise sunbeam_guard.BlockedExceptionError(
                        "WAL/DB storage is not supported with vaultlocker OSD encryption"
                    )
                self._reconcile_vaultlocker_osds(event)
                return

            storage_request = self._normalize_storage_config()
            logger.debug(
                "Normalized storage config request: %s",
                json.dumps(storage_request, sort_keys=True),
            )

            if not storage_request["osd_match"]:
                if self._has_ignored_waldb_config():
                    logger.info("WAL/DB settings ignored because no new OSDs are being added")
                logger.debug(
                    "osd-devices config not set, skipping config-based storage enrollment"
                )
                self._reset_osd_config_cache()
                self._set_storage_config_idle_status()
                return

            self._validate_storage_config(storage_request)

            if not self.charm.ready_for_service():
                logger.warning("MicroCeph not ready yet, deferring storage config processing")
                event.defer()
                return

            if self._is_cached_osd_config(storage_request):
                logger.debug(
                    "Skipping storage config processing because OSD-affecting inputs are "
                    "unchanged; exact repeats and WAL/DB-only changes both hit this path. "
                    "cacheable_request=%s signature=%s",
                    json.dumps(self._cacheable_osd_request(storage_request), sort_keys=True),
                    self._storage_config_signature(storage_request),
                )
                if self._storage_request_has_auxiliary_config(storage_request):
                    logger.info(
                        "Skipping config-driven WAL/DB apply because no new OSD selection was "
                        "detected; WAL/DB settings are only applied when new OSDs are added "
                        "osd_match=%s wal_enabled=%s db_enabled=%s",
                        storage_request["osd_match"],
                        bool(storage_request["wal_match"]),
                        bool(storage_request["db_match"]),
                    )
                self._set_storage_config_idle_status()
                return

            self._apply_osd_config(storage_request)

    def _normalize_storage_config(self) -> dict:
        """Normalize config-driven storage settings into a stable request dict."""
        raw_config = {
            "osd-devices": self.charm.model.config.get("osd-devices", ""),
            "wal-devices": self.charm.model.config.get("wal-devices", ""),
            "db-devices": self.charm.model.config.get("db-devices", ""),
            "wal-size": self.charm.model.config.get("wal-size", ""),
            "db-size": self.charm.model.config.get("db-size", ""),
            "device-add-flags": self.charm.model.config.get("device-add-flags", ""),
        }
        logger.debug(
            "Raw storage config values before normalization: %s",
            json.dumps(raw_config, sort_keys=True),
        )

        osd_match = self._normalized_config_value("osd-devices")
        if not osd_match:
            normalized = {
                "osd_match": None,
                "wal_match": None,
                "db_match": None,
                "wal_size": None,
                "db_size": None,
                "flags": asdict(DeviceAddFlags()),
            }
            logger.debug(
                "Normalized storage config without osd-devices: %s",
                json.dumps(normalized, sort_keys=True),
            )
            return normalized

        flags = self._parse_osd_device_flags(self.charm.model.config.get("device-add-flags", ""))
        wal_match = self._normalized_config_value("wal-devices")
        db_match = self._normalized_config_value("db-devices")
        raw_wal_size = self._normalized_config_value("wal-size")
        raw_db_size = self._normalized_config_value("db-size")

        wal_size = raw_wal_size if wal_match else None
        db_size = raw_db_size if db_match else None

        if not wal_match and (raw_wal_size or flags.wipe_wal or flags.encrypt_wal):
            logger.debug(
                "Dropping WAL size/flags because wal-devices is unset: raw_wal_size=%s "
                "wipe_wal=%s encrypt_wal=%s",
                raw_wal_size,
                flags.wipe_wal,
                flags.encrypt_wal,
            )
            flags.wipe_wal = False
            flags.encrypt_wal = False

        if not db_match and (raw_db_size or flags.wipe_db or flags.encrypt_db):
            logger.debug(
                "Dropping DB size/flags because db-devices is unset: raw_db_size=%s "
                "wipe_db=%s encrypt_db=%s",
                raw_db_size,
                flags.wipe_db,
                flags.encrypt_db,
            )
            flags.wipe_db = False
            flags.encrypt_db = False

        normalized = {
            "osd_match": osd_match,
            "wal_match": wal_match,
            "db_match": db_match,
            "wal_size": wal_size,
            "db_size": db_size,
            "flags": asdict(flags),
        }
        logger.debug(
            "Normalized storage config with osd-devices: %s",
            json.dumps(normalized, sort_keys=True),
        )
        return normalized

    def _normalized_config_value(self, key: str):
        """Trim a string config value and convert empty strings to None."""
        value = (self.charm.model.config.get(key, "") or "").strip()
        return value or None

    def _has_ignored_waldb_config(self) -> bool:
        """Whether WAL/DB config was provided without an OSD activation request."""
        configured_keys = [
            key
            for key in ("wal-devices", "db-devices", "wal-size", "db-size")
            if self._normalized_config_value(key)
        ]
        if configured_keys:
            logger.debug(
                "Detected WAL/DB config values without osd-devices: keys=%s",
                configured_keys,
            )
            return True

        waldb_flags = {"wipe:wal", "encrypt:wal", "wipe:db", "encrypt:db"}
        raw_flags = (self.charm.model.config.get("device-add-flags", "") or "").split(",")
        configured_flags = [
            flag.strip().lower()
            for flag in raw_flags
            if flag.strip() and flag.strip().lower() in waldb_flags
        ]
        if configured_flags:
            logger.debug(
                "Detected WAL/DB device-add-flags without osd-devices: flags=%s",
                configured_flags,
            )
            return True

        return False

    def _storage_request_has_auxiliary_config(self, storage_request: dict) -> bool:
        """Whether a normalized request contains any WAL/DB-specific settings."""
        flags = storage_request["flags"]
        return bool(
            storage_request["wal_match"]
            or storage_request["db_match"]
            or flags["wipe_wal"]
            or flags["encrypt_wal"]
            or flags["wipe_db"]
            or flags["encrypt_db"]
        )

    def _cacheable_osd_request(self, storage_request: dict) -> dict:
        """Return the subset of storage config that can trigger a new snap command."""
        return {
            "osd_match": storage_request["osd_match"],
            "flags": {
                "wipe_osd": storage_request["flags"]["wipe_osd"],
                "encrypt_osd": storage_request["flags"]["encrypt_osd"],
            },
        }

    def _storage_config_signature(self, storage_request: dict) -> str:
        """Build a stable signature for OSD-affecting storage inputs."""
        return json.dumps(
            self._cacheable_osd_request(storage_request),
            sort_keys=True,
            separators=(",", ":"),
        )

    def _reset_osd_config_cache(self):
        """Reset cache for last successfully applied config-driven storage request."""
        logger.debug(
            "Resetting config-driven storage cache previous_osd_match=%s previous_wipe=%s "
            "previous_encrypt=%s previous_signature=%s",
            self._stored.last_osd_devices,
            self._stored.last_wipe_osd,
            self._stored.last_encrypt_osd,
            self._stored.last_storage_config_signature,
        )
        self._stored.last_osd_devices = ""
        self._stored.last_wipe_osd = False
        self._stored.last_encrypt_osd = False
        self._stored.last_storage_config_signature = ""
        logger.debug("Reset config-driven storage cache")

    def _set_osd_config_cache(self, storage_request: dict):
        """Persist cache for last successfully applied config-driven storage request."""
        cacheable_request = self._cacheable_osd_request(storage_request)
        self._stored.last_osd_devices = cacheable_request["osd_match"]
        self._stored.last_wipe_osd = cacheable_request["flags"]["wipe_osd"]
        self._stored.last_encrypt_osd = cacheable_request["flags"]["encrypt_osd"]
        self._stored.last_storage_config_signature = self._storage_config_signature(
            storage_request
        )
        logger.debug(
            "Persisted storage config cache cacheable_request=%s signature=%s",
            json.dumps(cacheable_request, sort_keys=True),
            self._stored.last_storage_config_signature,
        )

    def _is_cached_osd_config(self, storage_request: dict) -> bool:
        """Check whether current config-driven storage request was already applied."""
        requested = self._cacheable_osd_request(storage_request)
        requested_signature = self._storage_config_signature(storage_request)
        last_signature = self._stored.last_storage_config_signature
        legacy_state = {
            "osd_match": self._stored.last_osd_devices,
            "wipe_osd": self._stored.last_wipe_osd,
            "encrypt_osd": self._stored.last_encrypt_osd,
        }
        logger.debug(
            "Checking storage config cache requested=%s requested_signature=%s "
            "stored_signature=%s legacy_state=%s",
            json.dumps(requested, sort_keys=True),
            requested_signature,
            last_signature,
            json.dumps(legacy_state, sort_keys=True),
        )

        if last_signature:
            if last_signature == requested_signature:
                logger.debug("Storage config cache hit via current signature")
                return True

            try:
                cached_request = json.loads(last_signature)
            except (TypeError, ValueError):
                cached_request = None
                logger.debug(
                    "Stored storage config signature is not parseable as JSON request: %r",
                    last_signature,
                )

            if isinstance(cached_request, dict):
                logger.debug(
                    "Parsed stored storage config signature into request=%s",
                    json.dumps(cached_request, sort_keys=True),
                )
                cached_flags = cached_request.get("flags") or {}
                if (
                    cached_request.get("osd_match") == requested["osd_match"]
                    and cached_flags.get("wipe_osd", False) == requested["flags"]["wipe_osd"]
                    and cached_flags.get("encrypt_osd", False) == requested["flags"]["encrypt_osd"]
                ):
                    logger.debug("Storage config cache hit via parsed legacy signature")
                    return True

        legacy_hit = (
            self._stored.last_osd_devices == requested["osd_match"]
            and self._stored.last_wipe_osd == requested["flags"]["wipe_osd"]
            and self._stored.last_encrypt_osd == requested["flags"]["encrypt_osd"]
        )
        if legacy_hit:
            logger.debug("Storage config cache hit via legacy stored fields")
            return True

        logger.debug("Storage config cache miss")
        return False

    def _set_storage_config_idle_status(self):
        """Clear config-driven storage status for no-op/recovery paths."""
        status = self.storage_config_status.status
        if isinstance(status, ActiveStatus) and not status.message:
            logger.debug("Storage-config status already active")
            return

        logger.debug("Restoring active status for storage-config idle path")
        self.storage_config_status.set(ActiveStatus(""))

    def _parse_osd_device_flags(self, device_add_flags: str) -> DeviceAddFlags:
        """Parse device-add-flags for config-driven storage handling."""
        try:
            return parse_device_add_flags(device_add_flags)
        except ValueError as e:
            raise sunbeam_guard.BlockedExceptionError(f"Invalid device-add-flags: {e}")

    def _validate_storage_config(self, storage_request: dict):
        """Validate the minimal charm-owned storage config combinations."""
        if storage_request["wal_match"] and not storage_request["wal_size"]:
            logger.info(
                "Blocking config-driven storage because wal-devices was set without wal-size "
                "osd_match=%s wal_match=%s",
                storage_request["osd_match"],
                storage_request["wal_match"],
            )
            logger.debug(
                "Invalid storage config detected: wal-devices is set without wal-size "
                "request=%s",
                json.dumps(storage_request, sort_keys=True),
            )
            raise sunbeam_guard.BlockedExceptionError(
                "Invalid storage config: wal-devices requires wal-size"
            )
        if storage_request["db_match"] and not storage_request["db_size"]:
            logger.info(
                "Blocking config-driven storage because db-devices was set without db-size "
                "osd_match=%s db_match=%s",
                storage_request["osd_match"],
                storage_request["db_match"],
            )
            logger.debug(
                "Invalid storage config detected: db-devices is set without db-size request=%s",
                json.dumps(storage_request, sort_keys=True),
            )
            raise sunbeam_guard.BlockedExceptionError(
                "Invalid storage config: db-devices requires db-size"
            )

    def _apply_osd_config(self, storage_request: dict):
        """Execute config-driven storage enrollment and cache successful requests."""
        logger.info(
            "Processing storage config request: %s",
            json.dumps(storage_request, sort_keys=True),
        )
        logger.info(
            "Applying config-driven storage osd_match=%s wal_enabled=%s db_enabled=%s "
            "wipe=%s encrypt=%s",
            storage_request["osd_match"],
            bool(storage_request["wal_match"]),
            bool(storage_request["db_match"]),
            storage_request["flags"]["wipe_osd"],
            storage_request["flags"]["encrypt_osd"],
        )
        try:
            self.storage_config_status.set(MaintenanceStatus("Processing storage config"))
            logger.debug(
                "Calling microceph.add_disk_match_cmd for request=%s",
                json.dumps(storage_request, sort_keys=True),
            )
            output = microceph.add_disk_match_cmd(
                osd_match=storage_request["osd_match"],
                wal_match=storage_request["wal_match"],
                wal_size=storage_request["wal_size"],
                db_match=storage_request["db_match"],
                db_size=storage_request["db_size"],
                wipe=storage_request["flags"]["wipe_osd"],
                encrypt=storage_request["flags"]["encrypt_osd"],
                wal_wipe=storage_request["flags"]["wipe_wal"],
                wal_encrypt=storage_request["flags"]["encrypt_wal"],
                db_wipe=storage_request["flags"]["wipe_db"],
                db_encrypt=storage_request["flags"]["encrypt_db"],
            )
            if output and output.strip():
                logger.info("Storage config command output:\n%s", output.strip())
            self.storage_config_status.set(ActiveStatus(""))
            self._set_osd_config_cache(storage_request)
            logger.info(
                "Successfully processed storage config osd_match=%s wal_enabled=%s "
                "db_enabled=%s",
                storage_request["osd_match"],
                bool(storage_request["wal_match"]),
                bool(storage_request["db_match"]),
            )
        except (CalledProcessError, TimeoutExpired) as e:
            err_msg = self._error_message(e)
            if "no devices matched" in err_msg.lower():
                logger.info(
                    "No devices matched config-driven OSD request request=%s",
                    json.dumps(storage_request, sort_keys=True),
                )
                self.storage_config_status.set(ActiveStatus(""))
                self._set_osd_config_cache(storage_request)
                return

            logger.error(
                "Failed to process storage config request=%s error=%s",
                json.dumps(storage_request, sort_keys=True),
                err_msg,
            )
            raise sunbeam_guard.BlockedExceptionError(f"Failed to add OSDs via config: {err_msg}")

    # helper functions

    def _to_lower_dict(self, input: dict) -> dict:
        """Makes the json keys compatible with sunbeam."""
        return {k.lower(): v for k, v in input.items()}

    def _fetch_filtered_storages(self, directives: list) -> list:
        """Provides a filtered list of attached storage devices."""
        filtered = []
        for device in self.juju_storage_list():
            if device.split("/")[0] in directives:
                filtered.append(device)

        return filtered

    def _is_safety_failure(self, err: str) -> bool:
        """Checks if the subprocess error is caused by safety check."""
        return "need at least 3 OSDs" in (err or "")

    def _error_message(self, exc: Exception) -> str:
        """Build an actionable error message from subprocess exceptions."""
        return getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)

    def _run(self, cmd: list) -> str:
        """Wrapper around subprocess run for storage commands."""
        process = run(cmd, capture_output=True, text=True, check=True, timeout=180)
        logger.debug(f"Command {' '.join(cmd)} finished; Output: {process.stdout}")
        return process.stdout

    def _enroll_disks_in_batch(self, disks: list):
        """Adds requested Disks to Microceph and stored state."""
        # Enroll OSDs
        disk_paths = map(
            lambda name: self.juju_storage_get(storage_id=name, attribute="location"), disks
        )
        logger.debug(f"Disk paths {disk_paths}")
        microceph.enroll_disks_as_osds(disk_paths)

        # Save OSD data using storage names.
        for disk in disks:
            self._save_osd_data(disk)

    def remove_osd(self, osd_num: int, force: bool = False):
        """Removes OSD from MicroCeph and from stored state."""
        try:
            microceph.remove_disk_cmd(osd_num, force)
            # if no errors while removing OSD, clean stale osd records.
            self._clean_stale_osd_data()
        except CalledProcessError as e:
            if force:
                # If forced removal was done, clean stale osd records.
                self._clean_stale_osd_data()
            raise e

    def _save_osd_data(self, disk_name: str):
        """Save OSD data to stored state mapping with juju storage names."""
        logger.debug(f"Entry stored state: {dict(self._stored.osd_data)}")
        disk_path = self.juju_storage_get(storage_id=disk_name, attribute="location")

        for osd in microceph.list_disk_cmd(host_only=True)["ConfiguredDisks"]:
            # get block device info using /dev/disk-by-id and lsblk.
            local_device = microceph._get_disk_info(osd["path"])

            # e.g. check 'vdd' in '/dev/vdd' and is for a local device
            if local_device["name"] in disk_path:
                logger.debug(f"Added OSD {osd['osd']} with Disk {disk_name}.")
                self._stored.osd_data[osd["osd"]] = {
                    "disk": disk_name,  # storage name for OSD device.
                }

        logger.debug(f"Exit stored state: {dict(self._stored.osd_data)}")

    def _get_vaultlocker_mapper_osd_id(self, mapper_path: str):
        """Return the configured OSD number for a Vaultlocker mapper, if any."""
        for osd in microceph.list_disk_cmd(host_only=True)["ConfiguredDisks"]:
            if osd["path"] == mapper_path:
                return osd["osd"]
        return None

    def _save_vaultlocker_osd_data(self, disk_name: str, mapper_path: str):
        """Map a mapper-backed OSD to its Juju storage attachment."""
        osd_num = self._get_vaultlocker_mapper_osd_id(mapper_path)
        if osd_num is None:
            raise ValueError(f"Could not find an OSD for Vaultlocker mapper {mapper_path}")
        self._stored.osd_data[osd_num] = {"disk": disk_name}

    def _get_osd_id(self, name: str):
        """Fetch the OSD number of consuming OSD, None is not used as OSD."""
        # storage name is of the form osd-standalone/2 etc.
        directive = name.split("/")[0]

        if directive == self.standalone:
            directive = "disk"

        logger.debug(self._stored.osd_data)
        logger.debug(f"Searching for disk {name}")

        for k, v in dict(self._stored.osd_data).items():
            # if value is not None.
            if v and v[directive] == name:
                return k  # key is the stored osd number.
        return None

    def _clean_stale_osd_data(self):
        """Compare with disk list and remove stale entries."""
        osds = [osd["osd"] for osd in microceph.list_disk_cmd()["ConfiguredDisks"]]

        for osd_num in dict(self._stored.osd_data).keys():
            if osd_num not in osds:
                val = self._stored.osd_data.pop(osd_num)
                logger.debug(f"Popped state data for {osd_num}: {val}.")

    # NOTE(utkarshbhatthere): 'storage-get' sometimes fires before
    # requested information is available.
    @retry(wait=wait_fixed(5), stop=stop_after_attempt(10))
    def juju_storage_get(self, storage_id=None, attribute=None):
        """Get storage attributes."""
        _args = ["storage-get", "--format=json"]
        if storage_id:
            _args.extend(("-s", storage_id))
        if attribute:
            _args.append(attribute)
        try:
            return json.loads(self._run(_args))
        except ValueError as e:
            logger.error(e)
            return None

    def juju_storage_list(self, storage_name=None):
        """List the storage IDs for the unit."""
        _args = ["storage-list", "--format=json"]
        if storage_name:
            _args.append(storage_name)
        try:
            return json.loads(self._run(_args))
        except ValueError as e:
            logger.error(e)
            return None
        except OSError as e:
            import errno

            if e.errno == errno.ENOENT:
                # storage-list does not exist
                return []
            raise
