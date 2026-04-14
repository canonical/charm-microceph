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
from unittest.mock import call, patch

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
    def test_update_cluster_configs_triggers_restart_when_last_alphabetical_unchanged(
        self, cclient
    ):
        """Regression test for GH#246: restart must fire exactly once when configs change.

        When TLS is enabled, rgw_keystone_url and rgw_keystone_verify_ssl change.
        """
        cclient.from_socket().cluster.get_config.return_value = [
            {"key": "rgw_keystone_url", "value": "http://dummy-ip", "wait": False},
            {"key": "rgw_keystone_verify_ssl", "value": "false", "wait": False},
            {"key": "rgw_swift_versioning_enabled", "value": "true", "wait": False},
        ]
        configs_to_update = {
            "rgw_keystone_url": "https://dummy-ip",  # changed
            "rgw_keystone_verify_ssl": "true",  # changed
            "rgw_swift_versioning_enabled": "true",  # NOT changed
        }
        microceph.update_cluster_configs(configs_to_update)

        cluster = cclient.from_socket().cluster
        # Exactly one restart must be triggered regardless of how many configs changed,
        # on the last changed key in sorted order, with all prior keys skipping restart.
        cluster.update_config.assert_has_calls(
            [
                call("rgw_keystone_url", "https://dummy-ip", True),
                call("rgw_keystone_verify_ssl", "true", False),
            ]
        )
        assert cluster.update_config.call_count == 2

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
    @patch("microceph.gethostname")
    @patch("microceph._az_flag_supported", new=lambda: True)
    def test_join_cluster_with_availability_zone(self, gethn, run_cmd):
        """Test join_cluster includes --availability-zone when snap supports it."""
        gethn.return_value = "host"
        run_cmd.return_value = ""  # not a member yet
        microceph.join_cluster("token", "10.10.10.10", availability_zone="az-2")
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "join",
                "token",
                "--microceph-ip",
                "10.10.10.10",
                "--availability-zone",
                "az-2",
            ]
        )

    @patch("utils.run_cmd")
    @patch("microceph._az_flag_supported", new=lambda: True)
    def test_bootstrap_cluster_with_availability_zone(self, run_cmd):
        """Test bootstrap_cluster includes --availability-zone when snap supports it."""
        microceph.bootstrap_cluster(
            micro_ip="10.0.0.10",
            public_net="10.0.0.0/24",
            cluster_net="10.0.0.0/24",
            availability_zone="az-1",
        )
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
                "--availability-zone",
                "az-1",
            ]
        )

    @patch("utils.run_cmd")
    def test_bootstrap_cluster_without_availability_zone(self, run_cmd):
        """Test bootstrap_cluster omits --availability-zone when not provided."""
        microceph.bootstrap_cluster(
            micro_ip="10.0.0.10",
            public_net="10.0.0.0/24",
            cluster_net="10.0.0.0/24",
        )
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ]
        )

    @patch("utils.run_cmd_with_input")
    @patch("microceph._az_flag_supported", new=lambda: True)
    def test_adopt_ceph_cluster_with_availability_zone(self, run_cmd):
        """Test adopt_ceph_cluster includes --availability-zone when snap supports it."""
        microceph.adopt_ceph_cluster(
            fsid="test-fsid",
            mon_hosts=["10.0.0.1"],
            admin_key="test-key",
            micro_ip="10.0.0.10",
            public_net="10.0.0.0/24",
            cluster_net="10.0.0.0/24",
            availability_zone="az-1",
        )
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "adopt",
                "-",
                "--fsid",
                "test-fsid",
                "--mon-hosts",
                "10.0.0.1",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
                "--availability-zone",
                "az-1",
            ],
            input_data="test-key",
        )

    @patch("utils.run_cmd_with_input")
    def test_adopt_ceph_cluster_without_availability_zone(self, run_cmd):
        """Test adopt_ceph_cluster omits --availability-zone when not provided."""
        microceph.adopt_ceph_cluster(
            fsid="test-fsid",
            mon_hosts=["10.0.0.1"],
            admin_key="test-key",
            micro_ip="10.0.0.10",
            public_net="10.0.0.0/24",
            cluster_net="10.0.0.0/24",
        )
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "adopt",
                "-",
                "--fsid",
                "test-fsid",
                "--mon-hosts",
                "10.0.0.1",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            input_data="test-key",
        )

    @patch("utils.run_cmd")
    @patch("microceph._az_flag_supported", new=lambda: False)
    def test_bootstrap_cluster_az_unsupported(self, run_cmd):
        """Test bootstrap_cluster omits --availability-zone when snap does not support it."""
        microceph.bootstrap_cluster(
            micro_ip="10.0.0.10",
            public_net="10.0.0.0/24",
            cluster_net="10.0.0.0/24",
            availability_zone="az-1",
        )
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ]
        )

    @patch("utils.run_cmd")
    @patch("microceph.gethostname")
    @patch("microceph._az_flag_supported", new=lambda: False)
    def test_join_cluster_az_unsupported(self, gethn, run_cmd):
        """Test join_cluster omits --availability-zone when snap does not support it."""
        gethn.return_value = "host"
        run_cmd.return_value = ""  # not a member yet
        microceph.join_cluster("token", "10.10.10.10", availability_zone="az-2")
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "join",
                "token",
                "--microceph-ip",
                "10.10.10.10",
            ]
        )

    @patch("utils.run_cmd_with_input")
    @patch("microceph._az_flag_supported", new=lambda: False)
    def test_adopt_ceph_cluster_az_unsupported(self, run_cmd):
        """Test adopt_ceph_cluster omits --availability-zone when snap does not support it."""
        microceph.adopt_ceph_cluster(
            fsid="test-fsid",
            mon_hosts=["10.0.0.1"],
            admin_key="test-key",
            micro_ip="10.0.0.10",
            public_net="10.0.0.0/24",
            cluster_net="10.0.0.0/24",
            availability_zone="az-1",
        )
        run_cmd.assert_called_with(
            cmd=[
                "microceph",
                "cluster",
                "adopt",
                "-",
                "--fsid",
                "test-fsid",
                "--mon-hosts",
                "10.0.0.1",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            input_data="test-key",
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
        run_cmd.assert_called_with(["microceph", "disk", "add", "loop,4G,3"], timeout=900)

    @patch("utils.run_cmd")
    def test_add_osd_cmd_with_wal(self, run_cmd):
        # Test with WAL device
        microceph.add_osd_cmd("loop,4G,3", wal_dev="/dev/sdb")
        run_cmd.assert_called_with(
            ["microceph", "disk", "add", "loop,4G,3", "--wal-device", "/dev/sdb", "--wal-wipe"],
            timeout=900,
        )

    @patch("utils.run_cmd")
    def test_add_osd_cmd_with_db(self, run_cmd):
        # Test with DB device
        microceph.add_osd_cmd("loop,4G,3", db_dev="/dev/sdc")
        run_cmd.assert_called_with(
            ["microceph", "disk", "add", "loop,4G,3", "--db-device", "/dev/sdc", "--db-wipe"],
            timeout=900,
        )

    @patch("utils.run_cmd")
    def test_add_osd_cmd_with_wipe(self, run_cmd):
        # Test with wipe flag
        microceph.add_osd_cmd("loop,4G,3", wipe=True)
        run_cmd.assert_called_with(
            ["microceph", "disk", "add", "loop,4G,3", "--wipe"], timeout=900
        )

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
            ],
            timeout=900,
        )

    @patch("microceph._setup_dm_crypt")
    @patch("utils.run_cmd")
    def test_add_disk_match_cmd_osd_only(self, run_cmd, setup_dm_crypt):
        """Test DSL-based disk add for OSD-only requests."""
        microceph.add_disk_match_cmd("eq(@type,'nvme')")
        run_cmd.assert_called_once_with(
            ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')"], timeout=900
        )
        setup_dm_crypt.assert_not_called()

    @patch("microceph._setup_dm_crypt")
    @patch("utils.run_cmd")
    def test_add_disk_match_cmd_with_wal(self, run_cmd, setup_dm_crypt):
        """Test DSL-based disk add with WAL flags."""
        microceph.add_disk_match_cmd(
            "eq(@type,'nvme')",
            wal_match="eq(@type,'ssd')",
            wal_size="20GiB",
            wal_wipe=True,
        )
        run_cmd.assert_called_once_with(
            [
                "microceph",
                "disk",
                "add",
                "--osd-match",
                "eq(@type,'nvme')",
                "--wal-match",
                "eq(@type,'ssd')",
                "--wal-size",
                "20GiB",
                "--wal-wipe",
            ],
            timeout=900,
        )
        setup_dm_crypt.assert_not_called()

    @patch("microceph._setup_dm_crypt")
    @patch("utils.run_cmd")
    def test_add_disk_match_cmd_with_db(self, run_cmd, setup_dm_crypt):
        """Test DSL-based disk add with DB flags."""
        microceph.add_disk_match_cmd(
            "eq(@type,'nvme')",
            db_match="eq(@type,'hdd')",
            db_size="40GiB",
            db_wipe=True,
        )
        run_cmd.assert_called_once_with(
            [
                "microceph",
                "disk",
                "add",
                "--osd-match",
                "eq(@type,'nvme')",
                "--db-match",
                "eq(@type,'hdd')",
                "--db-size",
                "40GiB",
                "--db-wipe",
            ],
            timeout=900,
        )
        setup_dm_crypt.assert_not_called()

    @patch("microceph._setup_dm_crypt")
    @patch("utils.run_cmd")
    def test_add_disk_match_cmd_with_wal_and_db(self, run_cmd, setup_dm_crypt):
        """Test DSL-based disk add with OSD, WAL, and DB flags."""
        microceph.add_disk_match_cmd(
            "and(eq(@type,'nvme'),ge(@size,100GiB))",
            wal_match="eq(@type,'ssd')",
            wal_size="20GiB",
            db_match="eq(@type,'hdd')",
            db_size="40GiB",
            wipe=True,
            dry_run=True,
            wal_wipe=True,
            db_encrypt=True,
        )
        run_cmd.assert_called_once_with(
            [
                "microceph",
                "disk",
                "add",
                "--osd-match",
                "and(eq(@type,'nvme'),ge(@size,100GiB))",
                "--wal-match",
                "eq(@type,'ssd')",
                "--wal-size",
                "20GiB",
                "--wal-wipe",
                "--db-match",
                "eq(@type,'hdd')",
                "--db-size",
                "40GiB",
                "--db-encrypt",
                "--wipe",
                "--dry-run",
            ],
            timeout=900,
        )
        setup_dm_crypt.assert_called_once_with()

    @patch("microceph._setup_dm_crypt")
    @patch("utils.run_cmd")
    def test_add_disk_match_cmd_ignores_orphaned_auxiliary_args(self, run_cmd, setup_dm_crypt):
        """WAL/DB size and flags are ignored when their match selectors are unset."""
        microceph.add_disk_match_cmd(
            "eq(@type,'nvme')",
            wal_size="20GiB",
            wal_wipe=True,
            wal_encrypt=True,
            db_size="40GiB",
            db_wipe=True,
            db_encrypt=True,
        )

        run_cmd.assert_called_once_with(
            ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')"], timeout=900
        )
        setup_dm_crypt.assert_not_called()

    @patch("microceph._setup_dm_crypt")
    @patch("utils.run_cmd")
    def test_add_disk_match_cmd_calls_dm_crypt_for_any_active_encryption(
        self, run_cmd, setup_dm_crypt
    ):
        """Any active encryption flag triggers dm-crypt setup exactly once."""
        for kwargs in (
            {"encrypt": True},
            {"wal_match": "eq(@type,'ssd')", "wal_size": "20GiB", "wal_encrypt": True},
            {"db_match": "eq(@type,'hdd')", "db_size": "40GiB", "db_encrypt": True},
        ):
            with self.subTest(kwargs=kwargs):
                run_cmd.reset_mock()
                setup_dm_crypt.reset_mock()
                microceph.add_disk_match_cmd("eq(@type,'nvme')", **kwargs)
                setup_dm_crypt.assert_called_once_with()
                run_cmd.assert_called_once()


class TestAZFlagSupported(unittest.TestCase):

    def setUp(self):
        microceph._az_flag_supported.cache_clear()

    @patch("subprocess.run")
    def test_returns_true_when_flag_in_help(self, mock_run):
        """Returns True when --availability-zone appears in bootstrap help output."""
        mock_run.return_value.stdout = (
            "Flags:\n  --availability-zone string   Availability zone for this node\n"
        )
        mock_run.return_value.stderr = ""
        self.assertTrue(microceph._az_flag_supported())

    @patch("subprocess.run")
    def test_returns_false_when_flag_absent(self, mock_run):
        """Returns False when --availability-zone is absent from bootstrap help output."""
        mock_run.return_value.stdout = "Flags:\n  --public-network string   Public network CIDR\n"
        mock_run.return_value.stderr = ""
        self.assertFalse(microceph._az_flag_supported())

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_false_when_binary_missing(self, _mock_run):
        """Returns False when the microceph binary is not installed."""
        self.assertFalse(microceph._az_flag_supported())
