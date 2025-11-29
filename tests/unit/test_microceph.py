# Copyright 2024 Canonical Ltd.
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

"""Tests for Microceph helper functions."""

import unittest
from unittest.mock import patch

import microceph


class TestMicroCeph(unittest.TestCase):

    @patch("microceph.Client")
    def test_is_rgw_enabled_service_not_running(self, cclient):
        """Test is_rgw_enabled when service is not running."""
        cclient.from_socket().cluster.list_services.return_value = [
            {"service": "mds", "location": "fake-host"},
            {"service": "mgr", "location": "fake-host"},
            {"service": "mon", "location": "fake-host"},
        ]
        self.assertFalse(microceph.is_rgw_enabled("fake-host"))

    @patch("microceph.Client")
    def test_is_rgw_enabled_service_running(self, cclient):
        """Test is_rgw_enabled when service running."""
        cclient.from_socket().cluster.list_services.return_value = [
            {"service": "mds", "location": "fake-host"},
            {"service": "mgr", "location": "fake-host"},
            {"service": "mon", "location": "fake-host"},
            {"service": "rgw", "location": "fake-host"},
        ]
        self.assertTrue(microceph.is_rgw_enabled("fake-host"))

    @patch("microceph.Client")
    def test_is_rgw_enabled_service_running_and_host_mismatch(self, cclient):
        """Test is_rgw_enabled with host mismatch."""
        cclient.from_socket().cluster.list_services.return_value = [
            {"service": "mds", "location": "fake-host"},
            {"service": "mgr", "location": "fake-host"},
            {"service": "mon", "location": "fake-host"},
            {"service": "rgw", "location": "fake-host-2"},
        ]
        self.assertFalse(microceph.is_rgw_enabled("fake-host"))

    @patch("microceph.Client")
    def test_update_cluster_configs(self, cclient):
        """Test update_cluster_configs."""
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "cluster_network", "value": "10.121.193.0/24", "wait": False},
            {"key": "osd_pool_default_crush_rule", "value": "1", "wait": False},
        ]
        configs_to_update = {"rgw_keystone_url": "http://dummy-ip"}
        microceph.update_cluster_configs(configs_to_update)

        cclient.from_socket().cluster.update_config.assert_called_with(
            "rgw_keystone_url", "http://dummy-ip", False
        )

    @patch("microceph.Client")
    def test_update_cluster_configs_with_some_configs_already_in_db(self, cclient):
        """Test update_cluster_configs with mix of configs in db and input."""
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "cluster_network", "value": "10.121.193.0/24", "wait": False},
            {"key": "osd_pool_default_crush_rule", "value": "1", "wait": False},
            {"key": "rgw_keystone_url", "value": "https://dummy-ip", "wait": False},
            {"key": "rgw_keystone_accepted_roles", "value": "admin", "wait": False},
        ]
        configs_to_update = {
            "rgw_keystone_url": "https://dummy-ip",
            "rgw_keystone_verify_ssl": "false",
            "rgw_keystone_accepted_roles": "Member,member",
        }
        microceph.update_cluster_configs(configs_to_update)

        configs_updated = [
            call.args[0] for call in cclient.from_socket().cluster.update_config.mock_calls
        ]
        assert "rgw_keystone_url" not in configs_updated
        assert "rgw_keystone_verify_ssl" in configs_updated
        assert "rgw_keystone_accepted_roles" in configs_updated

    @patch("microceph.Client")
    def test_update_cluster_configs_with_all_configs_already_in_db(self, cclient):
        """Test update_cluster_configs with all configs in db."""
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "cluster_network", "value": "10.121.193.0/24", "wait": False},
            {"key": "osd_pool_default_crush_rule", "value": "1", "wait": False},
            {"key": "rgw_keystone_url", "value": "https://dummy-ip", "wait": False},
            {"key": "rgw_keystone_verify_ssl", "value": "false", "wait": False},
        ]
        configs_to_update = {
            "rgw_keystone_url": "https://dummy-ip",
            "rgw_keystone_verify_ssl": "false",
        }
        microceph.update_cluster_configs(configs_to_update)

        cclient.from_socket().cluster.update_config.assert_not_called()

    @patch("microceph.Client")
    def test_delete_cluster_configs(self, cclient):
        """Test delete_cluster_configs with configs to delete not in db."""
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "cluster_network", "value": "10.121.193.0/24", "wait": False},
            {"key": "osd_pool_default_crush_rule", "value": "1", "wait": False},
        ]
        configs_to_delete = ["rgw_keystone_url"]
        microceph.delete_cluster_configs(configs_to_delete)

        cclient.from_socket().cluster.delete_config.assert_not_called()

    @patch("microceph.Client")
    def test_delete_cluster_configs_with_some_configs_already_in_db(self, cclient):
        """Test delete_cluster_configs with mix of configs to delete in db and input."""
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "cluster_network", "value": "10.121.193.0/24", "wait": False},
            {"key": "osd_pool_default_crush_rule", "value": "1", "wait": False},
            {"key": "rgw_keystone_url", "value": "https://dummy-ip", "wait": False},
            {"key": "rgw_keystone_accepted_roles", "value": "admin", "wait": False},
        ]
        configs_to_delete = ["rgw_keystone_url", "rgw_keystone_verify_ssl"]
        microceph.delete_cluster_configs(configs_to_delete)

        configs_deleted = [
            call.args[0] for call in cclient.from_socket().cluster.delete_config.mock_calls
        ]
        assert "rgw_keystone_url" in configs_deleted
        assert "rgw_keystone_verify_ssl" not in configs_deleted

    @patch("microceph.Client")
    def test_delete_cluster_configs_with_all_configs_already_in_db(self, cclient):
        """Test delete_cluster_configs with all rgw configs to be deleted."""
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "cluster_network", "value": "10.121.193.0/24", "wait": False},
            {"key": "osd_pool_default_crush_rule", "value": "1", "wait": False},
            {"key": "rgw_keystone_url", "value": "https://dummy-ip", "wait": False},
            {"key": "rgw_keystone_accepted_roles", "value": "admin", "wait": False},
        ]
        configs_to_delete = ["rgw_keystone_url", "rgw_keystone_accepted_roles"]
        microceph.delete_cluster_configs(configs_to_delete)

        configs_deleted = [
            call.args[0] for call in cclient.from_socket().cluster.delete_config.mock_calls
        ]
        assert "rgw_keystone_url" in configs_deleted
        assert "rgw_keystone_accepted_roles" in configs_deleted

    @patch("utils.run_cmd")
    @patch("microceph.gethostname")
    def test_join_cluster(self, gethn, run_cmd):
        """Test if cluster join is idempotent."""
        gethn.return_value = "host"
        run_cmd.return_value = "long status that contains host"
        microceph.join_cluster("token", "10.10.10.10")
        run_cmd.assert_called_with(["microceph", "status"])

        # status command will not contain hostname.
        run_cmd.return_value = ""
        microceph.join_cluster("token", "10.10.10.10")
        run_cmd.assert_called_with(
            cmd=["microceph", "cluster", "join", "token", "--microceph-ip", "10.10.10.10"]
        )

    @patch("utils.run_cmd")
    def test_enable_nfs(self, run_cmd):
        microceph.enable_nfs("foo", "lish", "addr")

        run_cmd.assert_called_once_with(
            [
                "microceph",
                "enable",
                "nfs",
                "--target",
                "foo",
                "--cluster-id",
                "lish",
                "--bind-address",
                "addr",
            ]
        )

    @patch("utils.run_cmd")
    def test_disable_nfs(self, run_cmd):
        microceph.disable_nfs("foo", "lish")

        run_cmd.assert_called_once_with(
            ["microceph", "disable", "nfs", "--target", "foo", "--cluster-id", "lish"]
        )

    @patch("utils.run_cmd")
    def test_microceph_has_service(self, run_cmd):
        output = """Service                  Startup   Current   Notes
        microceph.nfs            enabled   active    -
        """
        run_cmd.return_value = output

        result = microceph.microceph_has_service("foo")

        self.assertFalse(result)
        run_cmd.assert_called_once_with(["snap", "services", "microceph"])

        result = microceph.microceph_has_service("nfs")
        self.assertTrue(result)

    @patch("utils.run_cmd")
    def test_add_osd_cmd(self, run_cmd):
        # Test default call
        microceph.add_osd_cmd("loop,4G,3")
        run_cmd.assert_called_with(["microceph", "disk", "add", "loop,4G,3"])

    @patch("utils.run_cmd")
    def test_add_osd_cmd_with_wal(self, run_cmd):
        # Test with WAL device
        microceph.add_osd_cmd("loop,4G,3", wal_dev="/dev/sdb")
        run_cmd.assert_called_with(
            ["microceph", "disk", "add", "loop,4G,3", "--wal-device", "/dev/sdb", "--wal-wipe"]
        )

    @patch("utils.run_cmd")
    def test_add_osd_cmd_with_db(self, run_cmd):
        # Test with DB device
        microceph.add_osd_cmd("loop,4G,3", db_dev="/dev/sdc")
        run_cmd.assert_called_with(
            ["microceph", "disk", "add", "loop,4G,3", "--db-device", "/dev/sdc", "--db-wipe"]
        )

    @patch("utils.run_cmd")
    def test_add_osd_cmd_with_wipe(self, run_cmd):
        # Test with wipe flag
        microceph.add_osd_cmd("loop,4G,3", wipe=True)
        run_cmd.assert_called_with(["microceph", "disk", "add", "loop,4G,3", "--wipe"])

    @patch("utils.run_cmd")
    def test_add_osd_cmd_all_options(self, run_cmd):
        # Test with all options
        microceph.add_osd_cmd("loop,4G,3", wal_dev="/dev/sdb", db_dev="/dev/sdc", wipe=True)
        run_cmd.assert_called_with(
            [
                "microceph",
                "disk",
                "add",
                "loop,4G,3",
                "--wal-device",
                "/dev/sdb",
                "--wal-wipe",
                "--db-device",
                "/dev/sdc",
                "--db-wipe",
                "--wipe",
            ]
        )
