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
        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
            return_value=True,
        ), patch.object(
            self.harness.charm, "handle_config_leader_charm_upgrade"
        ) as mock_upgrade_handler:
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

    def test_update_status_non_leader_clears_stale_upgrade_health_blocked(self):
        self.harness.set_leader(False)
        self.harness.charm.status.set(
            BlockedStatus(f"{charm.cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN")
        )

        with patch.object(
            self.harness.charm.cluster_upgrades,
            "upgrade_requested",
            return_value=False,
        ), patch.object(
            self.harness.charm, "handle_config_leader_charm_upgrade"
        ) as mock_upgrade_handler:
            self.harness.charm.on.update_status.emit()

        self.assertIsInstance(self.harness.charm.status.status, ActiveStatus)
        mock_upgrade_handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
