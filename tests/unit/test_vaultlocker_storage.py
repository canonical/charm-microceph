# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for Vaultlocker-backed OSD storage."""

import json
from pathlib import Path
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import ops_sunbeam.test_utils as test_utils
from ops.model import BlockedStatus, WaitingStatus
from unit import testbase

import charm
from encrypted_device import StableDevice


class TestVaultlockerStorage(testbase.TestBaseCharm):
    """Tests for encrypted-device reconciliation of attached OSD storage."""

    PATCHES = ["subprocess"]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        with open("config.yaml", "r") as config_file:
            config_data = config_file.read()
        with open("metadata.yaml", "r") as metadata_file:
            metadata = metadata_file.read()
        self.harness = test_utils.get_harness(
            testbase._MicroCephCharm,
            container_calls=self.container_calls,
            charm_config=config_data,
            charm_metadata=metadata,
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.storage = self.harness.charm.storage
        self.ready_for_service = patch.object(
            self.harness.charm, "ready_for_service", return_value=True
        ).start()
        self.addCleanup(self.ready_for_service.stop)

    def _add_vaultlocker_relation(self) -> int:
        relation_id = self.harness.add_relation("encrypted-device", "vaultlocker")
        self.harness.add_relation_unit(relation_id, "vaultlocker/0")
        return relation_id

    def test_broken_vaultlocker_relation_blocks_managed_storage(self):
        """A removed provider cannot complete or safely replace active requests."""
        relation_id = self._add_vaultlocker_relation()
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": "/dev/disk/by-id/wwn-0x5000c500aabbcc01",
                "rdev": 2048,
                "relation_id": relation_id,
                "phase": "requested",
            }
        }

        self.harness.remove_relation(relation_id)

        status = self.storage.storage_status.status
        assert isinstance(status, BlockedStatus)
        assert (
            status.message == "Vaultlocker relation removed while encrypted OSD storage is managed"
        )

    def test_disabling_vaultlocker_with_managed_osd_storage_blocks(self):
        """An existing Vaultlocker OSD cannot be silently mixed with native enrollment."""
        self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "phase": "enrolled",
                "luks_uuid": "a1",
            }
        }

        self.harness.update_config({"osd-encryption-provider": "none"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == (
            "Cannot disable vaultlocker OSD encryption while Vaultlocker-managed OSD storage exists"
        )

    def test_unknown_osd_encryption_provider_blocks(self):
        """A typo must not silently fall back to unencrypted OSD enrollment."""
        self.harness.update_config({"osd-encryption-provider": "vaultlokcer"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "Invalid osd-encryption-provider: vaultlokcer"

    def test_osd_devices_is_rejected_before_normalization_in_vaultlocker_mode(self):
        """Vaultlocker mode never invokes the native OSD-device discovery path."""
        self._add_vaultlocker_relation()

        with patch.object(self.storage, "_normalize_storage_config") as normalize:
            self.harness.update_config(
                {
                    "osd-encryption-provider": "vaultlocker",
                    "osd-devices": "eq(@type,'ssd')",
                }
            )

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "osd-devices is not supported with vaultlocker OSD encryption"
        normalize.assert_not_called()

    def test_device_add_flags_are_rejected_in_vaultlocker_mode(self):
        """Config-driven native-encryption flags cannot be silently ignored."""
        self._add_vaultlocker_relation()

        self.harness.update_config(
            {
                "osd-encryption-provider": "vaultlocker",
                "device-add-flags": "encrypt:osd",
            }
        )

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert (
            status.message == "device-add-flags is not supported with vaultlocker OSD encryption"
        )

    def test_wal_db_configuration_is_rejected_in_vaultlocker_mode(self):
        """Vaultlocker v0 cannot safely provision MicroCeph WAL or DB devices."""
        self._add_vaultlocker_relation()

        with patch.object(self.storage, "_normalize_storage_config") as normalize:
            self.harness.update_config(
                {
                    "osd-encryption-provider": "vaultlocker",
                    "wal-devices": "eq(@type,'ssd')",
                    "wal-size": "20GiB",
                }
            )

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "WAL/DB storage is not supported with vaultlocker OSD encryption"
        normalize.assert_not_called()

    def test_add_osd_action_requests_vaultlocker_encryption(self):
        """An encrypted direct-device action publishes an asynchronous request."""
        relation_id = self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb,/dev/vdc",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }
        stable_devices = [
            StableDevice(path="/dev/disk/by-id/wwn-0x5000c500aabbcc01", rdev=2048),
            StableDevice(path="/dev/disk/by-id/wwn-0x5000c500aabbcc02", rdev=4096),
        ]

        with (
            patch("storage.resolve_stable_block_device", side_effect=stable_devices),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        relation_data = self.harness.get_relation_data(relation_id, self.harness.charm.unit.name)
        assert json.loads(relation_data["device_requests"]) == {
            stable_device.path: {} for stable_device in stable_devices
        }
        event.set_results.assert_called_once_with(
            {
                "result": [
                    {
                        "request-path": stable_devices[0].path,
                        "spec": "/dev/vdb",
                        "status": "pending",
                    },
                    {
                        "request-path": stable_devices[1].path,
                        "spec": "/dev/vdc",
                        "status": "pending",
                    },
                ]
            }
        )
        event.fail.assert_not_called()
        add_osd_cmd.assert_not_called()
        assert isinstance(self.storage.storage_status.status, WaitingStatus)

    def test_add_osd_action_reports_existing_completed_request(self):
        """Retrying an action reports completion rather than another pending request."""
        relation_id = self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            f"{self.storage.vaultlocker_action_prefix}{stable_path}": {
                "request_path": stable_path,
                "rdev": 2048,
                "relation_id": relation_id,
                "phase": "enrolled",
                "mapper_path": "/dev/mapper/crypt-a1b2c3d4",
                "luks_uuid": "a1b2c3d4",
                "source": "add-osd",
            }
        }
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }

        with (
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        event.set_results.assert_called_once_with(
            {
                "result": [
                    {
                        "request-path": stable_path,
                        "spec": "/dev/vdb",
                        "status": "enrolled",
                    }
                ]
            }
        )
        event.fail.assert_not_called()
        add_osd_cmd.assert_not_called()

    def test_add_osd_action_rejects_unencrypted_device_in_vaultlocker_mode(self):
        """Vaultlocker mode cannot silently fall back to a raw-device OSD."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb",
            "loop-spec": None,
            "wipe": False,
            "encrypt": False,
        }
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"

        with (
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        message = "add-osd requires encrypt=true with vaultlocker OSD encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_rejects_loop_spec_in_vaultlocker_mode(self):
        """Loop devices cannot provide the stable identity required by the relation."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": None,
            "loop-spec": "4G,3",
            "wipe": False,
            "encrypt": True,
        }

        with patch("storage.microceph.add_osd_cmd") as add_osd_cmd:
            self.storage._add_osd_action(event)

        message = "loop-spec is not supported with vaultlocker OSD encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_rejects_wipe_in_vaultlocker_mode(self):
        """Vaultlocker fresh encryption does not expose the native wipe operation."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb",
            "loop-spec": None,
            "wipe": True,
            "encrypt": True,
        }
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"

        with (
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        message = "wipe is not supported with vaultlocker OSD encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_requires_device_id_in_vaultlocker_mode(self):
        """A Vaultlocker request needs a concrete block device identity."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": None,
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }

        with patch("storage.microceph.add_osd_cmd") as add_osd_cmd:
            self.storage._add_osd_action(event)

        message = "device-id is required with vaultlocker OSD encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_requires_vaultlocker_relation(self):
        """The asynchronous action cannot proceed without a provider to consume it."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }

        with patch("storage.microceph.add_osd_cmd") as add_osd_cmd:
            self.storage._add_osd_action(event)

        message = "Vaultlocker relation is required for vaultlocker OSD encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_reports_unsafe_target(self):
        """A failed local preflight does not leave an asynchronous action request behind."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"

        with (
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch(
                "storage.validate_fresh_encryption_target",
                side_effect=ValueError("device is mounted"),
            ),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        message = "OSD storage is not safe for Vaultlocker encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_rejects_duplicate_device_in_one_batch(self):
        """A direct-device batch cannot publish the same underlying device twice."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb,/dev/vdc",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }
        stable_device = StableDevice(
            path="/dev/disk/by-id/wwn-0x5000c500aabbcc01",
            rdev=2048,
        )

        with (
            patch("storage.resolve_stable_block_device", return_value=stable_device),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        message = "add-osd includes the same block device more than once"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_rolls_back_partial_request_batch(self):
        """One invalid device does not publish another device from the same action."""
        self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb,/dev/vdc",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }
        stable_paths = [
            StableDevice(path="/dev/disk/by-id/wwn-0x5000c500aabbcc01", rdev=2048),
            StableDevice(path="/dev/disk/by-id/wwn-0x5000c500aabbcc02", rdev=4096),
        ]

        with (
            patch("storage.resolve_stable_block_device", side_effect=stable_paths),
            patch(
                "storage.validate_fresh_encryption_target",
                side_effect=[None, ValueError("device is mounted")],
            ),
            patch("storage.microceph.add_osd_cmd") as add_osd_cmd,
        ):
            self.storage._add_osd_action(event)

        message = "OSD storage is not safe for Vaultlocker encryption"
        event.set_results.assert_called_once_with({"message": message})
        event.fail.assert_called_once_with(message)
        add_osd_cmd.assert_not_called()
        assert not self.storage._stored.vaultlocker_devices

    def test_add_osd_action_result_enrolls_returned_mapper(self):
        """A matching result completes an asynchronous add-osd request once."""
        relation_id = self._add_vaultlocker_relation()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.peers.interface.state.joined = True
        with patch.object(self.storage, "_clean_stale_osd_data"):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        mapper_path = "/dev/mapper/crypt-a1b2c3d4"
        event = MagicMock()
        event.params = {
            "device-id": "/dev/vdb",
            "loop-spec": None,
            "wipe": False,
            "encrypt": True,
        }

        with (
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_fresh_encryption_target"),
        ):
            self.storage._add_osd_action(event)

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(self.storage, "_fetch_filtered_storages", return_value=[]),
            patch("storage.validate_mapper_block_device"),
            patch("storage.microceph.ensure_dm_crypt") as ensure_dm_crypt,
            patch("storage.microceph.enroll_disks_as_osds") as enroll_disks,
            patch.object(self.storage, "_save_vaultlocker_osd_data"),
            patch.object(self.storage, "_update_vaultlocker_boot_order"),
        ):
            self.harness.update_relation_data(
                relation_id,
                "vaultlocker/0",
                {
                    "device_results": json.dumps(
                        {
                            stable_path: {
                                "mapper_path": mapper_path,
                                "luks_uuid": "a1b2c3d4",
                            }
                        }
                    )
                },
            )

        ensure_dm_crypt.assert_called_once_with()
        enroll_disks.assert_called_once_with([mapper_path])
        request = next(iter(self.storage._stored.vaultlocker_devices.values()))
        assert request["phase"] == "enrolled"
        assert request["mapper_path"] == mapper_path

    def test_boot_order_dropin_tracks_enrolled_vaultlocker_osds(self):
        """Completed OSDs make the snap service wait for their unlock units."""
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "phase": "enrolled",
                "luks_uuid": "a1",
            },
            "osd-standalone/1": {
                "phase": "requested",
            },
            "osd-standalone/2": {
                "phase": "enrolled",
                "luks_uuid": "b2",
            },
        }

        with TemporaryDirectory() as directory:
            dropin = Path(directory) / "snap.microceph.osd.service.d" / "vaultlocker.conf"
            self.storage.vaultlocker_osd_dropin = dropin
            with patch.object(self.storage, "_run") as run:
                self.storage._update_vaultlocker_boot_order()

            assert dropin.read_text() == (
                "# Managed by charm-microceph. Do not edit.\n"
                "[Unit]\n"
                "After=vaultlocker-decrypt@a1.service vaultlocker-decrypt@b2.service\n"
            )
            run.assert_called_once_with(["systemctl", "daemon-reload"])

    def test_boot_order_retries_daemon_reload_after_a_failure(self):
        """A written drop-in is retried until systemd has loaded it."""
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {"phase": "enrolled", "luks_uuid": "a1"}
        }

        with TemporaryDirectory() as directory:
            dropin = Path(directory) / "snap.microceph.osd.service.d" / "vaultlocker.conf"
            self.storage.vaultlocker_osd_dropin = dropin
            with patch.object(
                self.storage,
                "_run",
                side_effect=CalledProcessError(1, ["systemctl", "daemon-reload"]),
            ):
                with self.assertRaises(CalledProcessError):
                    self.storage._update_vaultlocker_boot_order()

            assert self.storage._stored.vaultlocker_boot_order_dirty
            with patch.object(self.storage, "_run") as run:
                self.storage._update_vaultlocker_boot_order()

            run.assert_called_once_with(["systemctl", "daemon-reload"])
            assert not self.storage._stored.vaultlocker_boot_order_dirty

    def test_boot_order_dropin_is_removed_when_no_osds_remain(self):
        """The OSD service stops ordering against removed Vaultlocker devices."""
        self.storage._stored.vaultlocker_devices = {"osd-standalone/0": {"phase": "requested"}}

        with TemporaryDirectory() as directory:
            dropin = Path(directory) / "snap.microceph.osd.service.d" / "vaultlocker.conf"
            dropin.parent.mkdir()
            dropin.write_text("[Unit]\nAfter=vaultlocker-decrypt@old.service\n")
            self.storage.vaultlocker_osd_dropin = dropin
            with patch.object(self.storage, "_run") as run:
                self.storage._update_vaultlocker_boot_order()

            assert not dropin.exists()
            run.assert_called_once_with(["systemctl", "daemon-reload"])

    def test_detaching_pending_storage_withdraws_vaultlocker_request(self):
        """Detaching before a result withdraws the uncompleted request."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "phase": "requested",
            }
        }
        relation = self.harness.charm.model.get_relation("encrypted-device")
        relation.data[self.harness.charm.unit]["device_requests"] = json.dumps({stable_path: {}})
        event = MagicMock()
        event.storage.full_id = "osd-standalone/0"

        with patch.object(self.storage, "remove_osd") as remove_osd:
            self.storage._on_storage_detaching(event)

        assert "osd-standalone/0" not in self.storage._stored.vaultlocker_devices
        relation_data = self.harness.get_relation_data(relation_id, self.harness.charm.unit.name)
        assert json.loads(relation_data["device_requests"]) == {}
        remove_osd.assert_not_called()

    def test_detaching_incomplete_enrollment_withdraws_vaultlocker_request(self):
        """A storage detach stops a request even after mapper enrollment started."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "phase": "enrolling",
                "mapper_path": "/dev/mapper/crypt-a1",
                "luks_uuid": "a1",
            }
        }
        event = MagicMock()
        event.storage.full_id = "osd-standalone/0"

        with (
            patch.object(self.storage, "_get_vaultlocker_mapper_osd_id", return_value=None),
            patch.object(self.storage, "remove_osd") as remove_osd,
        ):
            self.storage._on_storage_detaching(event)

        assert "osd-standalone/0" not in self.storage._stored.vaultlocker_devices
        relation_data = self.harness.get_relation_data(relation_id, self.harness.charm.unit.name)
        assert json.loads(relation_data["device_requests"]) == {}
        remove_osd.assert_not_called()

    def test_detaching_unmapped_completed_osd_recovers_its_osd_number(self):
        """A crash before local state save cannot strand a mapper-backed OSD."""
        storage_name = "osd-standalone/0"
        self.storage._stored.vaultlocker_devices = {
            storage_name: {
                "request_path": "/dev/disk/by-id/wwn-0x5000c500aabbcc01",
                "rdev": 2048,
                "phase": "enrolled",
                "mapper_path": "/dev/mapper/crypt-a1",
                "luks_uuid": "a1",
            }
        }
        event = MagicMock()
        event.storage.full_id = storage_name

        with (
            patch("storage.utils.is_departing", return_value=False),
            patch.object(self.storage, "_get_vaultlocker_mapper_osd_id", return_value=5),
            patch.object(self.storage, "remove_osd") as remove_osd,
            patch.object(self.storage, "_update_vaultlocker_boot_order"),
        ):
            self.storage._on_storage_detaching(event)

        remove_osd.assert_called_once_with(5)
        assert storage_name not in self.storage._stored.vaultlocker_devices

    def test_force_detach_withdraws_completed_vaultlocker_request(self):
        """Forced OSD removal cannot leave a stale request or boot dependency behind."""
        storage_name = "osd-standalone/0"
        self.storage._stored.osd_data = {5: {"disk": storage_name}}
        self.storage._stored.vaultlocker_devices = {
            storage_name: {
                "request_path": "/dev/disk/by-id/wwn-0x5000c500aabbcc01",
                "rdev": 2048,
                "phase": "enrolled",
                "luks_uuid": "a1",
            }
        }
        event = MagicMock()
        event.storage.full_id = storage_name
        safety_error = CalledProcessError(
            returncode=1,
            cmd=["microceph", "disk", "remove"],
            stderr="need at least 3 OSDs",
        )

        with (
            patch("storage.utils.is_departing", return_value=False),
            patch.object(self.storage, "remove_osd", side_effect=[safety_error, None]),
            patch.object(self.storage, "_update_vaultlocker_boot_order") as update_boot_order,
        ):
            self.storage._on_storage_detaching(event)

        assert storage_name not in self.storage._stored.vaultlocker_devices
        update_boot_order.assert_called_once_with()

    def test_attached_storage_publishes_fresh_vaultlocker_request(self):
        """Vaultlocker mode publishes an empty request and waits for its result."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.microceph.enroll_disks_as_osds") as enroll_disks,
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
            self.storage._on_osd_standalone_attached(MagicMock())

        relation_data = self.harness.get_relation_data(relation_id, self.harness.charm.unit.name)
        assert json.loads(relation_data["device_requests"]) == {stable_path: {}}
        enroll_disks.assert_not_called()
        assert isinstance(self.storage.storage_status.status, WaitingStatus)

    def test_matching_vaultlocker_result_enrolls_returned_mapper(self):
        """A successful result is consumed as a mapper-backed OSD without --encrypt."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        mapper_path = "/dev/mapper/crypt-a1b2c3d4"
        self.harness.update_relation_data(
            relation_id,
            "vaultlocker/0",
            {
                "device_results": json.dumps(
                    {
                        stable_path: {
                            "mapper_path": mapper_path,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        phase_when_dm_crypt_is_prepared = []

        def record_dm_crypt_phase():
            phase_when_dm_crypt_is_prepared.append(
                self.storage._stored.vaultlocker_devices["osd-standalone/0"]["phase"]
            )

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.validate_mapper_block_device"),
            patch(
                "storage.microceph.ensure_dm_crypt",
                side_effect=record_dm_crypt_phase,
            ) as ensure_dm_crypt,
            patch("storage.microceph.enroll_disks_as_osds") as enroll_disks,
            patch.object(self.storage, "_save_vaultlocker_osd_data") as save_osd_data,
            patch.object(self.storage, "_update_vaultlocker_boot_order") as update_boot_order,
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
            self.storage._on_osd_standalone_attached(MagicMock())

        ensure_dm_crypt.assert_called_once_with()
        assert phase_when_dm_crypt_is_prepared == ["requested"]
        enroll_disks.assert_called_once_with([mapper_path])
        save_osd_data.assert_called_once_with("osd-standalone/0", mapper_path)
        update_boot_order.assert_called()
        request = self.storage._stored.vaultlocker_devices["osd-standalone/0"]
        assert request["phase"] == "enrolled"
        assert request["relation_id"] == relation_id
        assert request["mapper_path"] == mapper_path
        assert request["luks_uuid"] == "a1b2c3d4"

    def test_changed_completed_vaultlocker_result_blocks(self):
        """A completed request is immutable and cannot be remapped by the provider."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "relation_id": relation_id,
                "phase": "enrolled",
                "mapper_path": "/dev/mapper/crypt-a1",
                "luks_uuid": "a1",
            }
        }
        self.storage._stored.osd_data = {5: {"disk": "osd-standalone/0"}}
        self.harness.update_relation_data(
            relation_id,
            "vaultlocker/0",
            {
                "device_results": json.dumps(
                    {
                        stable_path: {
                            "mapper_path": "/dev/mapper/crypt-b2",
                            "luks_uuid": "b2",
                        }
                    }
                )
            },
        )

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_mapper_block_device"),
            patch.object(self.storage, "_update_vaultlocker_boot_order"),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "Vaultlocker changed a completed device result"

    def test_enrolling_request_recovers_osd_mapping_without_readding_disk(self):
        """A hook retry never repeats a disk add after an enrollment was started."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        mapper_path = "/dev/mapper/crypt-a1b2c3d4"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "relation_id": relation_id,
                "phase": "enrolling",
                "mapper_path": mapper_path,
                "luks_uuid": "a1b2c3d4",
            }
        }
        self.harness.update_relation_data(
            relation_id,
            "vaultlocker/0",
            {
                "device_results": json.dumps(
                    {
                        stable_path: {
                            "mapper_path": mapper_path,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_mapper_block_device"),
            patch("storage.microceph.ensure_dm_crypt") as ensure_dm_crypt,
            patch("storage.microceph.enroll_disks_as_osds") as enroll_disks,
            patch.object(self.storage, "_save_vaultlocker_osd_data") as save_osd_data,
            patch.object(self.storage, "_update_vaultlocker_boot_order"),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
            self.storage._on_osd_standalone_attached(MagicMock())

        ensure_dm_crypt.assert_not_called()
        enroll_disks.assert_not_called()
        save_osd_data.assert_called_once_with("osd-standalone/0", mapper_path)
        assert self.storage._stored.vaultlocker_devices["osd-standalone/0"]["phase"] == "enrolled"

    def test_unsafe_fresh_target_blocks_vaultlocker_request(self):
        """Verify that the charm rejects unsafe local storage before publishing a request."""
        self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch(
                "storage.validate_fresh_encryption_target",
                side_effect=ValueError("device is mounted"),
            ),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "OSD storage is not safe for Vaultlocker encryption"
        assert not self.storage._stored.vaultlocker_devices

    def test_missing_stable_device_identity_blocks_vaultlocker_request(self):
        """Reject a Juju attachment that cannot be safely named in relation data."""
        self._add_vaultlocker_relation()

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                side_effect=ValueError("no stable by-id path"),
            ),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "OSD storage does not have a stable /dev/disk/by-id identity"
        assert not self.storage._stored.vaultlocker_devices

    def test_duplicate_vaultlocker_device_request_blocks(self):
        """Reject a second Juju storage attachment that resolves to the same device."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "relation_id": relation_id,
                "phase": "requested",
            }
        }

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/1"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdc"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(
                    path="/dev/disk/by-id/wwn-0x5000c500aabbcc02",
                    rdev=2048,
                ),
            ),
            patch("storage.validate_fresh_encryption_target"),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "The same block device is already requested through Vaultlocker"

    def test_changed_vaultlocker_relation_blocks_managed_storage(self):
        """A request cannot be transferred to a different Vaultlocker application."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "relation_id": relation_id + 1,
                "phase": "enrolled",
                "mapper_path": "/dev/mapper/crypt-a1",
                "luks_uuid": "a1",
            }
        }
        self.storage._stored.osd_data = {5: {"disk": "osd-standalone/0"}}

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch.object(self.storage, "_update_vaultlocker_boot_order"),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "Vaultlocker relation changed while OSD storage is managed"

    def test_changed_attachment_identity_blocks_pending_vaultlocker_request(self):
        """A stable symlink that now resolves to another disk is never reused."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.storage._stored.vaultlocker_devices = {
            "osd-standalone/0": {
                "request_path": stable_path,
                "rdev": 2048,
                "relation_id": relation_id,
                "phase": "requested",
            }
        }

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=4096),
            ),
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})

        status = self.storage.storage_config_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "Juju storage attachment changed during Vaultlocker provisioning"

    def test_invalid_vaultlocker_result_blocks_without_enrolling_osd(self):
        """Verify that the charm does not consume an incomplete Vaultlocker result."""
        relation_id = self._add_vaultlocker_relation()
        stable_path = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
        self.harness.update_relation_data(
            relation_id,
            "vaultlocker/0",
            {"device_results": json.dumps({stable_path: {"luks_uuid": "a1b2c3d4"}})},
        )

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/0"],
            ),
            patch.object(self.storage, "juju_storage_get", return_value="/dev/vdb"),
            patch(
                "storage.resolve_stable_block_device",
                return_value=StableDevice(path=stable_path, rdev=2048),
            ),
            patch("storage.validate_fresh_encryption_target"),
            patch("storage.microceph.enroll_disks_as_osds") as enroll_disks,
        ):
            self.harness.update_config({"osd-encryption-provider": "vaultlocker"})
            self.storage._on_osd_standalone_attached(MagicMock())

        status = self.storage.storage_status.status
        assert isinstance(status, BlockedStatus)
        assert status.message == "Invalid Vaultlocker device result"
        enroll_disks.assert_not_called()
