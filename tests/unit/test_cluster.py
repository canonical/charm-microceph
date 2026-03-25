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

"""Tests for the cluster module — add_node_to_cluster UNIQUE constraint handling."""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

import cluster


class TestAddNodeToCluster(unittest.TestCase):
    """Tests for ClusterNodes.add_node_to_cluster."""

    def _make_cluster_nodes(self):
        """Create a ClusterNodes instance with mocked charm."""
        charm_mock = MagicMock()
        charm_mock.peers.get_all_unit_values.return_value = ["test-hostname"]
        charm_mock.peers.set_app_data = MagicMock()
        # ClusterNodes.__init__ calls super().__init__ which needs a framework
        with patch.object(cluster.ops.framework.Object, "__init__"):
            cn = cluster.ClusterNodes.__new__(cluster.ClusterNodes)
            cn.charm = charm_mock
        return cn

    def _make_event(self):
        event = MagicMock()
        event.unit = MagicMock()
        event.unit.name = "microceph/1"
        return event

    def _make_called_process_error(self, stderr):
        """Create a CalledProcessError with the given stderr."""
        err = subprocess.CalledProcessError(1, "microceph cluster add")
        err.stderr = stderr
        return err

    @patch("utils.run_cmd")
    def test_add_node_success(self, run_cmd):
        """Normal add succeeds and sets join token."""
        run_cmd.return_value = "test-token\n"
        cn = self._make_cluster_nodes()
        event = self._make_event()

        cn.add_node_to_cluster(event)

        run_cmd.assert_called_once_with(["microceph", "cluster", "add", "test-hostname"])
        cn.charm.peers.set_app_data.assert_called_once_with(
            {"microceph/1.join_token": "test-token"}
        )

    @patch("utils.run_cmd")
    def test_add_node_unique_constraint_core_token_records(self, run_cmd):
        """UNIQUE constraint on core_token_records (squid+) is handled gracefully."""
        run_cmd.side_effect = self._make_called_process_error(
            'Error: Failed to create "core_token_records" entry: '
            "UNIQUE constraint failed: core_token_records.name"
        )
        cn = self._make_cluster_nodes()
        event = self._make_event()

        # Should NOT raise
        cn.add_node_to_cluster(event)
        cn.charm.peers.set_app_data.assert_not_called()

    @patch("utils.run_cmd")
    def test_add_node_unique_constraint_internal_token_records(self, run_cmd):
        """UNIQUE constraint on internal_token_records (pre-squid) is handled gracefully."""
        run_cmd.side_effect = self._make_called_process_error(
            'Error: Failed to create "internal_token_records" entry: '
            "UNIQUE constraint failed: internal_token_records.name"
        )
        cn = self._make_cluster_nodes()
        event = self._make_event()

        # Should NOT raise
        cn.add_node_to_cluster(event)
        cn.charm.peers.set_app_data.assert_not_called()

    @patch("utils.run_cmd")
    def test_add_node_unique_constraint_future_table_name(self, run_cmd):
        """UNIQUE constraint on a hypothetical future table name is also handled."""
        run_cmd.side_effect = self._make_called_process_error(
            'Error: Failed to create "whatever_token_records" entry: '
            "UNIQUE constraint failed: whatever_token_records.name"
        )
        cn = self._make_cluster_nodes()
        event = self._make_event()

        # Should NOT raise — substring match covers any *_token_records table
        cn.add_node_to_cluster(event)
        cn.charm.peers.set_app_data.assert_not_called()

    @patch("utils.run_cmd")
    def test_add_node_other_unique_constraint_raises(self, run_cmd):
        """A UNIQUE constraint on a non-token_records table should still raise."""
        run_cmd.side_effect = self._make_called_process_error(
            "UNIQUE constraint failed: some_other_table.column"
        )
        cn = self._make_cluster_nodes()
        event = self._make_event()

        with self.assertRaises(subprocess.CalledProcessError):
            cn.add_node_to_cluster(event)

    @patch("utils.run_cmd")
    def test_add_node_other_error_raises(self, run_cmd):
        """Non-UNIQUE errors should propagate."""
        run_cmd.side_effect = self._make_called_process_error(
            "Error: something completely different went wrong"
        )
        cn = self._make_cluster_nodes()
        event = self._make_event()

        with self.assertRaises(subprocess.CalledProcessError):
            cn.add_node_to_cluster(event)

    @patch("utils.run_cmd")
    def test_add_node_timeout_error_raises(self, run_cmd):
        """Timeout errors should propagate."""
        err = subprocess.TimeoutExpired("microceph cluster add", 30)
        err.stderr = ""
        run_cmd.side_effect = err
        cn = self._make_cluster_nodes()
        event = self._make_event()

        with self.assertRaises(subprocess.TimeoutExpired):
            cn.add_node_to_cluster(event)

    def test_add_node_no_unit(self):
        """Event without unit should return early."""
        cn = self._make_cluster_nodes()
        event = MagicMock()
        event.unit = None

        cn.add_node_to_cluster(event)
        cn.charm.peers.get_all_unit_values.assert_not_called()

    def test_add_node_no_hostname(self):
        """No hostname found should return early."""
        cn = self._make_cluster_nodes()
        cn.charm.peers.get_all_unit_values.return_value = []
        event = self._make_event()

        cn.add_node_to_cluster(event)
        cn.charm.peers.set_app_data.assert_not_called()


if __name__ == "__main__":
    unittest.main()
