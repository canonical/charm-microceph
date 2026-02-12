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
from subprocess import CalledProcessError, TimeoutExpired, run

import ops_sunbeam.guard as sunbeam_guard
from ops.charm import ActionEvent, CharmBase, StorageAttachedEvent, StorageDetachingEvent
from ops.framework import Object, StoredState
from ops.model import ActiveStatus, MaintenanceStatus
from tenacity import retry, stop_after_attempt, wait_fixed

import microceph
from device_flags import DeviceAddFlags, parse_device_add_flags

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
        )
        self.charm = charm
        self.name = name

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

        self._clean_stale_osd_data()

        enroll = []

        logger.debug(f"storage on unit: {self._fetch_filtered_storages([self.standalone])}")

        for storage in self._fetch_filtered_storages([self.standalone]):
            logger.debug(f"Processing {storage}")
            if not self._get_osd_id(name=storage):
                enroll.append(storage)

        logger.debug(f"Enroll list {enroll}")
        with sunbeam_guard.guard(self.charm, self.name):
            self.charm.status.set(MaintenanceStatus("Enrolling OSDs"))
            self._enroll_disks_in_batch(enroll)
            self.charm.status.set(ActiveStatus("charm is ready"))

    def _on_storage_detaching(self, event: StorageDetachingEvent):
        """Unified storage detaching handler."""
        # check if the detaching device (of the form directive/index)
        # is being used as or with an OSD.
        logger.debug(f"Detach event received for : {event.storage.full_id}")
        osd_num = self._get_osd_id(event.storage.full_id)

        logger.debug(f"OSD ID for: {event.storage.full_id} is {osd_num}")
        if osd_num is None:
            return

        with sunbeam_guard.guard(self.charm, self.name):
            try:
                self.remove_osd(osd_num)
            except CalledProcessError as e:
                err_msg = self._error_message(e)
                if self._is_safety_failure(err_msg):
                    warning = f"Storage {event.storage.full_id} detached, provide replacement for osd.{osd_num}."
                    logger.warning(warning)
                    # forcefully remove OSD and entry from stored state
                    # because Juju WILL deprovision storage.
                    self.remove_osd(osd_num, force=True)
                    raise sunbeam_guard.BlockedExceptionError(warning)

    def _add_osd_action(self, event: ActionEvent):
        """Add OSD disks to microceph."""
        if not self.charm.peers.interface.state.joined:
            event.set_results({"message": "Node not yet joined in microceph cluster"})
            event.fail()
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
                logger.error("Failed add-osd for spec=%s wipe=%s: %s", spec, wipe, err_msg)
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
        """Process osd-devices config option.

        This method is called on config-changed events to enroll matching
        devices as OSDs. The DSL expression is passed to the MicroCeph snap
        which performs device matching and enrollment.
        """
        osd_devices = self.charm.model.config.get("osd-devices", "")
        device_add_flags = self.charm.model.config.get("device-add-flags", "")
        osd_match = osd_devices.strip()

        # Skip if osd-devices is not configured
        if not osd_match:
            logger.debug("osd-devices config not set, skipping config-based OSD enrollment")
            self._reset_osd_config_cache()
            return

        # Wait for cluster to be ready
        if not self.charm.ready_for_service():
            logger.warning("MicroCeph not ready yet, deferring osd-devices processing")
            event.defer()
            return

        # Execute the OSD match command
        with sunbeam_guard.guard(self.charm, self.name):
            flags = self._parse_osd_device_flags(device_add_flags)
            if self._is_cached_osd_config(osd_match, flags):
                logger.debug(
                    "Skipping osd-devices processing: unchanged config osd_match=%s wipe=%s encrypt=%s",
                    osd_match,
                    flags.wipe_osd,
                    flags.encrypt_osd,
                )
                return

            self._apply_osd_config(osd_match, flags)

    def _reset_osd_config_cache(self):
        """Reset cache for last successfully applied osd-devices configuration."""
        self._stored.last_osd_devices = ""
        self._stored.last_wipe_osd = False
        self._stored.last_encrypt_osd = False
        logger.debug("Reset osd-devices config cache")

    def _set_osd_config_cache(self, osd_match: str, flags: DeviceAddFlags):
        """Persist cache for last successfully applied osd-devices configuration."""
        self._stored.last_osd_devices = osd_match
        self._stored.last_wipe_osd = flags.wipe_osd
        self._stored.last_encrypt_osd = flags.encrypt_osd
        logger.debug(
            "Updated osd-devices config cache osd_match=%s wipe=%s encrypt=%s",
            osd_match,
            flags.wipe_osd,
            flags.encrypt_osd,
        )

    def _is_cached_osd_config(self, osd_match: str, flags: DeviceAddFlags) -> bool:
        """Check whether current osd-devices configuration was already applied."""
        return (
            self._stored.last_osd_devices == osd_match
            and self._stored.last_wipe_osd == flags.wipe_osd
            and self._stored.last_encrypt_osd == flags.encrypt_osd
        )

    def _parse_osd_device_flags(self, device_add_flags: str) -> DeviceAddFlags:
        """Parse device-add-flags for osd-devices handling."""
        try:
            return parse_device_add_flags(device_add_flags)
        except ValueError as e:
            raise sunbeam_guard.BlockedExceptionError(f"Invalid device-add-flags: {e}")

    def _apply_osd_config(self, osd_match: str, flags: DeviceAddFlags):
        """Execute OSD enrollment via osd-match and cache successful configs."""
        logger.info(
            "Processing osd-devices config osd_match=%s wipe=%s encrypt=%s",
            osd_match,
            flags.wipe_osd,
            flags.encrypt_osd,
        )
        try:
            self.charm.status.set(MaintenanceStatus("Processing osd-devices config"))
            microceph.add_osd_match_cmd(
                osd_match=osd_match,
                wipe=flags.wipe_osd,
                encrypt=flags.encrypt_osd,
            )
            self.charm.status.set(ActiveStatus("charm is ready"))
            self._set_osd_config_cache(osd_match, flags)
            logger.info("Successfully processed osd-devices config: %s", osd_match)
        except (CalledProcessError, TimeoutExpired) as e:
            err_msg = self._error_message(e)
            # "no devices matched" is not an error - it's a valid scenario
            if "no devices matched" in err_msg.lower():
                logger.info("No devices matched osd-devices DSL expression")
                self.charm.status.set(ActiveStatus("charm is ready"))
                self._set_osd_config_cache(osd_match, flags)
                return

            logger.error("Failed to process osd-devices config %s: %s", osd_match, err_msg)
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
        return getattr(exc, "stderr", None) or str(exc)

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
