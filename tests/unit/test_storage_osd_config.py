# Copyright 2025 Canonical Ltd.
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

"""Unit tests for StorageHandler config-driven storage reconciliation."""

from subprocess import CalledProcessError
from unittest.mock import MagicMock, patch

import ops_sunbeam.test_utils as test_utils
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from unit import testbase

import charm


class TestConfigDrivenStorage(testbase.TestBaseCharm):
    """Tests for the config-changed storage handler."""

    PATCHES = ["subprocess"]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        with open("config.yaml", "r") as f:
            config_data = f.read()
        with open("metadata.yaml", "r") as f:
            metadata = f.read()
        self.harness = test_utils.get_harness(
            testbase._MicroCephCharm,
            container_calls=self.container_calls,
            charm_config=config_data,
            charm_metadata=metadata,
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.storage = self.harness.charm.storage

        patcher = patch.object(self.harness.charm, "ready_for_service", return_value=False)
        self.ready_for_service = patcher.start()
        self.addCleanup(patcher.stop)

    def _setup_ready_charm(self):
        """Set up peer relation and mock ready_for_service to return True."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness._charm.peers.interface.state.joined = True
        self.ready_for_service.return_value = True

    def _call_handler(self, event=None):
        """Call the config-changed handler with a mock event."""
        if event is None:
            event = MagicMock()
        self.storage._on_config_changed_osd_devices(event)
        return event

    def _storage_config_status(self):
        """Return the storage-config status slot."""
        return self.storage.storage_config_status.status

    def _storage_status(self):
        """Return the storage status slot."""
        return self.storage.storage_status.status

    def _workload_status(self):
        """Return the shared workload status slot."""
        return self.harness.charm.status.status

    def test_attached_storage_restores_ready_workload_message(self):
        """Successful Juju storage events should restore a human-readable ready message."""
        self._setup_ready_charm()
        self.harness.charm.status.set(ActiveStatus(""))
        event = MagicMock()

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/6"],
            ),
            patch.object(self.storage, "_get_osd_id", return_value=None),
            patch.object(self.storage, "_enroll_disks_in_batch"),
        ):
            self.storage._on_osd_standalone_attached(event)

        event.defer.assert_not_called()
        self.assertIsInstance(self._storage_status(), ActiveStatus)
        self.assertEqual(self._storage_status().message, "")
        self.assertIsInstance(self._workload_status(), ActiveStatus)
        self.assertEqual(self._workload_status().message, "charm is ready")

    def test_storage_detaching_skips_osd_removal_on_whole_app_teardown(self):
        """Storage detaching should skip OSD removal when the whole app is removed."""
        # planned_units() == 0 is how the handler detects whole-app teardown.
        self.harness.set_planned_units(0)
        # full_id identifies the detaching storage; _get_osd_id below maps it to an OSD.
        event = MagicMock()
        event.storage.full_id = "osd-standalone/0"

        # _get_osd_id -> 5 ensures we get past the "no OSD for this storage" early return,
        # so the only thing that can stop remove_osd is the teardown guard itself.
        with (
            patch.object(self.storage, "_get_osd_id", return_value=5),
            patch.object(self.storage, "remove_osd") as remove_osd,
        ):
            self.storage._on_storage_detaching(event)

        # Guard fired: it short-circuits before touching the (quorum-less) cluster.
        remove_osd.assert_not_called()

    def test_attached_storage_does_not_clobber_blocked_workload_status(self):
        """Storage attach should not clear unrelated workload failures."""
        self._setup_ready_charm()
        self.harness.charm.status.set(BlockedStatus("Improper value for config enable-rgw"))
        event = MagicMock()

        with (
            patch.object(self.storage, "_clean_stale_osd_data"),
            patch.object(
                self.storage,
                "_fetch_filtered_storages",
                return_value=["osd-standalone/6"],
            ),
            patch.object(self.storage, "_get_osd_id", return_value=None),
            patch.object(self.storage, "_enroll_disks_in_batch"),
        ):
            self.storage._on_osd_standalone_attached(event)

        self.assertIsInstance(self._workload_status(), BlockedStatus)
        self.assertEqual(
            self._workload_status().message,
            "Improper value for config enable-rgw",
        )

    def test_normalize_storage_config_trims_and_includes_waldb(self):
        """Normalization trims whitespace and includes WAL/DB settings."""
        self.harness.update_config(
            {
                "osd-devices": "  eq(@type,'nvme')  ",
                "wal-devices": " eq(@type,'ssd') ",
                "db-devices": " eq(@type,'hdd') ",
                "wal-size": " 20GiB ",
                "db-size": " 40GiB ",
                "device-add-flags": " wipe:osd , encrypt:wal , wipe:db ",
            }
        )

        self.assertEqual(
            self.storage._normalize_storage_config(),
            {
                "osd_match": "eq(@type,'nvme')",
                "wal_match": "eq(@type,'ssd')",
                "db_match": "eq(@type,'hdd')",
                "wal_size": "20GiB",
                "db_size": "40GiB",
                "flags": {
                    "wipe_osd": True,
                    "encrypt_osd": False,
                    "wipe_wal": False,
                    "encrypt_wal": True,
                    "wipe_db": True,
                    "encrypt_db": False,
                },
            },
        )

    def test_normalize_storage_config_ignores_waldb_without_osd(self):
        """WAL/DB config is ignored entirely when osd-devices is empty."""
        self.harness.update_config(
            {
                "osd-devices": "   ",
                "wal-devices": "eq(@type,'ssd')",
                "db-devices": "eq(@type,'hdd')",
                "wal-size": "20GiB",
                "db-size": "40GiB",
                "device-add-flags": "wipe:wal,encrypt:db",
            }
        )

        self.assertEqual(
            self.storage._normalize_storage_config(),
            {
                "osd_match": None,
                "wal_match": None,
                "db_match": None,
                "wal_size": None,
                "db_size": None,
                "flags": {
                    "wipe_osd": False,
                    "encrypt_osd": False,
                    "wipe_wal": False,
                    "encrypt_wal": False,
                    "wipe_db": False,
                    "encrypt_db": False,
                },
            },
        )

    def test_normalize_storage_config_discards_inactive_auxiliary_settings(self):
        """WAL/DB sizes and flags are ignored when their device selectors are unset."""
        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "",
                "db-devices": "   ",
                "wal-size": "20GiB",
                "db-size": "40GiB",
                "device-add-flags": "wipe:osd,wipe:wal,encrypt:wal,wipe:db,encrypt:db",
            }
        )

        self.assertEqual(
            self.storage._normalize_storage_config(),
            {
                "osd_match": "eq(@type,'nvme')",
                "wal_match": None,
                "db_match": None,
                "wal_size": None,
                "db_size": None,
                "flags": {
                    "wipe_osd": True,
                    "encrypt_osd": False,
                    "wipe_wal": False,
                    "encrypt_wal": False,
                    "wipe_db": False,
                    "encrypt_db": False,
                },
            },
        )

    def test_empty_osd_devices_resets_signature_cache(self):
        """Clearing osd-devices clears both legacy and signature cache fields."""
        self.storage._stored.last_osd_devices = "eq(@type,'nvme')"
        self.storage._stored.last_wipe_osd = True
        self.storage._stored.last_encrypt_osd = True
        self.storage._stored.last_storage_config_signature = "cached-signature"

        self.harness.update_config(
            {
                "osd-devices": "",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
                "device-add-flags": "wipe:wal",
            }
        )

        self.assertEqual(self.storage._stored.last_osd_devices, "")
        self.assertFalse(self.storage._stored.last_wipe_osd)
        self.assertFalse(self.storage._stored.last_encrypt_osd)
        self.assertEqual(self.storage._stored.last_storage_config_signature, "")

    def test_not_ready_defers(self):
        """Configured storage defers while the cluster is not ready."""
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        event = self._call_handler()

        event.defer.assert_called_once()

    @patch("storage.microceph.add_disk_match_cmd")
    def test_unchanged_signature_skips_snap_call(self, add_disk_match_cmd):
        """Cached storage config is not applied again."""
        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
                "device-add-flags": "wipe:osd,encrypt:wal",
            }
        )
        request = self.storage._normalize_storage_config()
        self.storage._stored.last_storage_config_signature = (
            self.storage._storage_config_signature(request)
        )
        self._setup_ready_charm()

        self._call_handler()

        add_disk_match_cmd.assert_not_called()

    @patch("storage.microceph.add_disk_match_cmd")
    def test_legacy_osd_cache_fields_skip_waldb_only_change(self, add_disk_match_cmd):
        """Legacy OSD cache fields still suppress WAL/DB-only replays after upgrade."""
        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
            }
        )
        self.storage._stored.last_osd_devices = "eq(@type,'nvme')"
        self.storage._stored.last_wipe_osd = False
        self.storage._stored.last_encrypt_osd = False
        self.storage._stored.last_storage_config_signature = ""
        self._setup_ready_charm()

        self.harness.update_config({"wal-size": "30GiB"})
        self._call_handler()

        add_disk_match_cmd.assert_not_called()
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")

    @patch("storage.microceph.add_disk_match_cmd")
    def test_waldb_change_without_osd_delta_skips_snap_call(self, add_disk_match_cmd):
        """Changing WAL/DB settings alone does not re-emit the snap command."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
            }
        )
        add_disk_match_cmd.reset_mock()

        self.harness.update_config({"wal-size": "30GiB"})

        add_disk_match_cmd.assert_not_called()
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")

    @patch("storage.microceph.add_disk_match_cmd")
    def test_clearing_auxiliary_match_without_osd_delta_skips_snap_call(self, add_disk_match_cmd):
        """Removing WAL/DB selectors alone does not re-emit the snap command."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
                "device-add-flags": "wipe:osd,wipe:wal,encrypt:wal",
            }
        )
        add_disk_match_cmd.reset_mock()

        self.harness.update_config({"wal-devices": "", "wal-size": "20GiB"})

        add_disk_match_cmd.assert_not_called()
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")

    @patch("storage.microceph.add_disk_match_cmd")
    def test_osd_change_applies_latest_waldb_settings(self, add_disk_match_cmd):
        """The next OSD delta reuses the latest WAL/DB settings."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@vendor,'intel')",
                "wal-size": "20GiB",
            }
        )
        add_disk_match_cmd.reset_mock()

        self.harness.update_config({"wal-size": "30GiB"})
        add_disk_match_cmd.assert_not_called()

        self.harness.update_config({"osd-devices": "eq(@type,'ssd')"})

        add_disk_match_cmd.assert_called_once_with(
            osd_match="eq(@type,'ssd')",
            wal_match="eq(@vendor,'intel')",
            wal_size="30GiB",
            db_match=None,
            db_size=None,
            wipe=False,
            encrypt=False,
            wal_wipe=False,
            wal_encrypt=False,
            db_wipe=False,
            db_encrypt=False,
        )

    @patch("storage.microceph.add_disk_match_cmd")
    def test_success_passes_waldb_request_and_updates_status(self, add_disk_match_cmd):
        """Successful reconciliation sends normalized flags to microceph."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = (
            "WAL match expression resolved to no devices; proceeding without WAL"
        )

        with patch.object(
            self.storage.storage_config_status,
            "set",
            wraps=self.storage.storage_config_status.set,
        ) as set_status:
            self.harness.update_config(
                {
                    "osd-devices": " eq(@type,'nvme') ",
                    "wal-devices": " eq(@type,'ssd') ",
                    "db-devices": " eq(@type,'hdd') ",
                    "wal-size": " 20GiB ",
                    "db-size": " 40GiB ",
                    "device-add-flags": "wipe:osd,encrypt:wal,wipe:db",
                }
            )

        add_disk_match_cmd.assert_called_once_with(
            osd_match="eq(@type,'nvme')",
            wal_match="eq(@type,'ssd')",
            wal_size="20GiB",
            db_match="eq(@type,'hdd')",
            db_size="40GiB",
            wipe=True,
            encrypt=False,
            wal_wipe=False,
            wal_encrypt=True,
            db_wipe=True,
            db_encrypt=False,
        )
        self.assertTrue(self.storage._stored.last_storage_config_signature)
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")
        self.assertTrue(
            any(
                isinstance(call.args[0], MaintenanceStatus)
                and call.args[0].message == "Processing storage config"
                for call in set_status.call_args_list
            )
        )
        self.assertTrue(
            any(
                isinstance(call.args[0], ActiveStatus) and call.args[0].message == ""
                for call in set_status.call_args_list
            )
        )

    @patch("storage.microceph.add_disk_match_cmd")
    def test_no_devices_matched_stays_active(self, add_disk_match_cmd):
        """No matching OSD devices is treated as a no-op, not a failure."""
        self._setup_ready_charm()
        add_disk_match_cmd.side_effect = CalledProcessError(
            returncode=1,
            cmd=["microceph"],
            stderr="Error: no devices matched the expression",
        )

        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")
        self.assertTrue(self.storage._stored.last_storage_config_signature)

    def test_unrelated_blocked_status_survives_empty_osd_idle_path(self):
        """Storage-config idle handling must not clear workload status."""
        self._setup_ready_charm()

        self.harness.update_config({"enable-rgw": "invalid"})

        self.assertIsInstance(self._workload_status(), BlockedStatus)
        self.assertEqual(
            self._workload_status().message,
            "Improper value for config enable-rgw",
        )
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_unrelated_blocked_status_survives_cached_osd_idle_path(self, add_disk_match_cmd):
        """Cached storage no-op must not clear workload status."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})
        self.assertTrue(self.storage._stored.last_storage_config_signature)

        self.harness.update_config({"enable-rgw": "invalid"})

        add_disk_match_cmd.assert_called_once_with(
            osd_match="eq(@type,'nvme')",
            wal_match=None,
            wal_size=None,
            db_match=None,
            db_size=None,
            wipe=False,
            encrypt=False,
            wal_wipe=False,
            wal_encrypt=False,
            db_wipe=False,
            db_encrypt=False,
        )
        self.assertIsInstance(self._workload_status(), BlockedStatus)
        self.assertEqual(
            self._workload_status().message,
            "Improper value for config enable-rgw",
        )
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_unrelated_blocked_status_survives_storage_apply(self, add_disk_match_cmd):
        """Storage reconciliation should use its own status slot."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config(
            {
                "enable-rgw": "invalid",
                "osd-devices": "eq(@type,'nvme')",
            }
        )

        add_disk_match_cmd.assert_called_once_with(
            osd_match="eq(@type,'nvme')",
            wal_match=None,
            wal_size=None,
            db_match=None,
            db_size=None,
            wipe=False,
            encrypt=False,
            wal_wipe=False,
            wal_encrypt=False,
            db_wipe=False,
            db_encrypt=False,
        )
        self.assertTrue(self.storage._stored.last_storage_config_signature)
        self.assertIsInstance(self._workload_status(), BlockedStatus)
        self.assertEqual(
            self._workload_status().message,
            "Improper value for config enable-rgw",
        )
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_storage_failure_uses_storage_config_status_slot(self, add_disk_match_cmd):
        """Storage failures should block storage-config without clobbering workload."""
        self._setup_ready_charm()
        add_disk_match_cmd.side_effect = CalledProcessError(
            returncode=1,
            cmd=["microceph"],
            stderr="WAL carrier overlaps selected OSD device",
        )

        self.harness.update_config(
            {
                "enable-rgw": "invalid",
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
            }
        )

        self.assertIsInstance(self._workload_status(), BlockedStatus)
        self.assertEqual(
            self._workload_status().message,
            "Improper value for config enable-rgw",
        )
        self.assertIsInstance(self._storage_config_status(), BlockedStatus)
        self.assertIn("WAL carrier overlaps", self._storage_config_status().message)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_missing_wal_size_blocks_without_snap_call(self, add_disk_match_cmd):
        """wal-devices requires wal-size."""
        self._setup_ready_charm()

        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "",
            }
        )

        add_disk_match_cmd.assert_not_called()
        self.assertIsInstance(self._storage_config_status(), BlockedStatus)
        self.assertIn("wal-size", self._storage_config_status().message)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_reverting_to_cached_request_clears_storage_block(self, add_disk_match_cmd):
        """Returning to the last good request clears stale blocked status."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)

        self.harness.update_config(
            {
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "",
            }
        )
        self.assertIsInstance(self._storage_config_status(), BlockedStatus)

        self.harness.update_config({"wal-devices": "", "wal-size": ""})

        self.assertEqual(add_disk_match_cmd.call_count, 1)
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")

    @patch("storage.microceph.add_disk_match_cmd")
    def test_clearing_osd_devices_clears_storage_block(self, add_disk_match_cmd):
        """Clearing config-driven storage activation removes stale storage blocks."""
        self._setup_ready_charm()
        add_disk_match_cmd.return_value = "configured"

        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})
        self.assertTrue(self.storage._stored.last_storage_config_signature)

        self.harness.update_config(
            {
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "",
            }
        )
        self.assertIsInstance(self._storage_config_status(), BlockedStatus)

        self.harness.update_config({"osd-devices": "", "wal-devices": "", "wal-size": ""})

        self.assertEqual(self.storage._stored.last_storage_config_signature, "")
        self.assertEqual(add_disk_match_cmd.call_count, 1)
        self.assertIsInstance(self._storage_config_status(), ActiveStatus)
        self.assertEqual(self._storage_config_status().message, "")

    @patch("storage.microceph.add_disk_match_cmd")
    def test_missing_db_size_blocks_without_snap_call(self, add_disk_match_cmd):
        """db-devices requires db-size."""
        self._setup_ready_charm()

        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "db-devices": "eq(@type,'hdd')",
                "db-size": "",
            }
        )

        add_disk_match_cmd.assert_not_called()
        self.assertIsInstance(self._storage_config_status(), BlockedStatus)
        self.assertIn("db-size", self._storage_config_status().message)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_invalid_flags_block_without_snap_call(self, add_disk_match_cmd):
        """Invalid device-add-flags block before any snap call."""
        self._setup_ready_charm()

        self.harness.update_config(
            {"osd-devices": "eq(@type,'nvme')", "device-add-flags": "encrypt:wla"}
        )

        add_disk_match_cmd.assert_not_called()
        self.assertIsInstance(self._storage_config_status(), BlockedStatus)
        self.assertIn("Invalid device-add-flags", self._storage_config_status().message)

    @patch("storage.microceph.add_disk_match_cmd")
    def test_snap_validation_failure_blocks(self, add_disk_match_cmd):
        """Snap validation errors surface as blocked status."""
        self._setup_ready_charm()
        add_disk_match_cmd.side_effect = CalledProcessError(
            returncode=1,
            cmd=["microceph"],
            stderr="WAL carrier overlaps selected OSD device",
        )

        self.harness.update_config(
            {
                "osd-devices": "eq(@type,'nvme')",
                "wal-devices": "eq(@type,'ssd')",
                "wal-size": "20GiB",
            }
        )

        self.assertIsInstance(self._storage_config_status(), BlockedStatus)
        self.assertIn("WAL carrier overlaps", self._storage_config_status().message)
