# Copyright 2026 Canonical Ltd.
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

"""Unit tests for update-status upgrade reconciliation."""

import unittest
from unittest.mock import patch

import ops_sunbeam.test_utils as test_utils
from ops.model import ActiveStatus, BlockedStatus
from unit import testbase

import charm


class TestUpdateStatusUpgradeReconcile(testbase.TestBaseCharm):
    def setUp(self):
        super().setUp(charm, [])

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
        self.harness.set_leader()

        patcher = patch.object(self.harness.charm, "ready_for_service")
        self.ready_for_service = patcher.start()
        self.ready_for_service.return_value = False
        self.addCleanup(patcher.stop)

    def test_update_status_retries_pending_upgrade(self):
        self.ready_for_service.return_value = True
        with (
            patch.object(
                self.harness.charm.cluster_upgrades,
                "upgrade_requested",
                return_value=True,
            ),
            patch.object(
                self.harness.charm, "handle_config_leader_charm_upgrade"
            ) as mock_upgrade_handler,
        ):
            self.harness.charm.on.update_status.emit()

        mock_upgrade_handler.assert_called_once()

    def test_update_status_clears_stale_upgrade_health_blocked(self):
        self.harness.charm.status.set(
            BlockedStatus(f"{charm.cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN")
        )

        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
            side_effect=[False, False],
        ):
            self.harness.charm.on.update_status.emit()

        self.assertIsInstance(self.harness.charm.status.status, ActiveStatus)

    def _update_status_with_snapd_error(self, mock_snap_client, error):
        mock_snap_client.return_value.get_snap_information.side_effect = error
        self.ready_for_service.return_value = True

        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
            return_value=True,
        ):
            self.harness.charm.on.update_status.emit()

        status = self.harness.charm.status.status
        self.assertIsInstance(status, BlockedStatus)
        self.assertTrue(
            status.message.startswith(charm.cluster.UPGRADE_SNAP_QUERY_BLOCKED_MSG_PREFIX),
            status.message,
        )
        return status.message

    @patch("microceph.snap.SnapClient")
    def test_update_status_pending_upgrade_snapd_timeout_blocks(self, mock_snap_client):
        """A snapd timeout during the upgrade check blocks the unit, no hook error."""
        msg = self._update_status_with_snapd_error(mock_snap_client, TimeoutError("timed out"))
        self.assertIn("TimeoutError", msg)

    @patch("microceph.snap.SnapClient")
    def test_update_status_pending_upgrade_snapd_api_error_blocks(self, mock_snap_client):
        """A snapd API error (e.g. snap not found) also blocks the unit, no hook error."""
        error = charm.microceph.snap.SnapAPIError({}, 404, "Not Found", "snap not found")
        msg = self._update_status_with_snapd_error(mock_snap_client, error)
        self.assertIn("SnapAPIError", msg)

    @patch("microceph.snap.SnapClient")
    def test_update_status_pending_upgrade_resumes_when_snapd_recovers(self, mock_snap_client):
        """Once snapd answers again, the next update-status starts the upgrade."""
        get_info = mock_snap_client.return_value.get_snap_information
        get_info.side_effect = TimeoutError("timed out")
        self.ready_for_service.return_value = True
        upgrades = self.harness.charm.cluster_upgrades
        pending = {"value": True}

        def _init_upgrade(snap_chan):
            pending["value"] = False

        with (
            patch.object(upgrades, "upgrade_requested", side_effect=lambda _: pending["value"]),
            patch.object(upgrades, "init_upgrade", side_effect=_init_upgrade) as mock_init,
            patch("cluster.CephStatus") as mock_ceph_status,
        ):
            mock_ceph_status.return_value.ceph_health.return_value = (
                charm.cluster.CephHealth.Ok,
                "",
            )
            self.harness.charm.on.update_status.emit()
            self.assertIsInstance(self.harness.charm.status.status, BlockedStatus)
            mock_init.assert_not_called()

            # snapd can query the store again.
            get_info.side_effect = None
            get_info.return_value = {
                "name": "microceph",
                "tracks": ["squid", "tentacle"],
                "channels": {},
            }
            self.harness.charm.on.update_status.emit()

        snap_chan = self.harness.charm.model.config.get("snap-channel")
        mock_init.assert_called_once_with(snap_chan)
        self.assertIsInstance(self.harness.charm.status.status, ActiveStatus)

    def test_update_status_clears_stale_upgrade_store_blocked(self):
        self.harness.charm.status.set(
            BlockedStatus(f"{charm.cluster.UPGRADE_SNAP_QUERY_BLOCKED_MSG_PREFIX}: timed out")
        )

        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
            side_effect=[False, False],
        ):
            self.harness.charm.on.update_status.emit()

        self.assertIsInstance(self.harness.charm.status.status, ActiveStatus)

    def test_update_status_does_not_clear_unrelated_blocked_status(self):
        self.harness.set_leader(False)
        self.harness.charm.status.set(BlockedStatus("waiting for something else"))

        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
        ) as mock_upgrade_requested:
            self.harness.charm.on.update_status.emit()

        status = self.harness.charm.status.status
        self.assertIsInstance(status, BlockedStatus)
        self.assertEqual(status.message, "waiting for something else")
        mock_upgrade_requested.assert_not_called()

    def test_update_status_does_not_clear_upgrade_health_blocked_when_upgrade_pending(self):
        self.harness.set_leader(False)
        self.harness.charm.status.set(
            BlockedStatus(f"{charm.cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN")
        )

        snap_chan = self.harness.charm.model.config.get("snap-channel")
        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
            return_value=True,
        ) as mock_upgrade_requested:
            self.harness.charm.on.update_status.emit()

        status = self.harness.charm.status.status
        self.assertIsInstance(status, BlockedStatus)
        self.assertIn(charm.cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX, status.message)
        mock_upgrade_requested.assert_called_once_with(snap_chan)

    def test_update_status_non_leader_clears_stale_upgrade_health_blocked(self):
        self.harness.set_leader(False)
        self.harness.charm.status.set(
            BlockedStatus(f"{charm.cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN")
        )

        with (
            patch.object(
                self.harness.charm.cluster_upgrades,
                "upgrade_requested",
                return_value=False,
            ),
            patch.object(
                self.harness.charm, "handle_config_leader_charm_upgrade"
            ) as mock_upgrade_handler,
        ):
            self.harness.charm.on.update_status.emit()

        self.assertIsInstance(self.harness.charm.status.status, ActiveStatus)
        mock_upgrade_handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
