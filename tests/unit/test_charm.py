# Copyright 2023 Canonical Ltd.
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

"""Tests for Microceph charm."""

from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import MagicMock, PropertyMock, call, mock_open, patch

import ops_sunbeam.test_utils as test_utils
from charms.ceph_mon.v0 import ceph_cos_agent
from unit import testbase

import charm
import microceph
from microceph_client import MaintenanceOperationFailedException


class TestCharm(testbase.TestBaseCharm):
    PATCHES = ["subprocess"]

    def setUp(self):
        """Setup MicroCeph Charm tests."""
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

    @patch.object(ceph_cos_agent, "CephCOSAgentProvider")
    @patch.object(ceph_cos_agent, "ceph_utils")
    @patch.object(microceph, "Client")
    @patch("utils.subprocess")
    @patch.object(Path, "chmod")
    @patch.object(Path, "write_bytes")
    @patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
    def test_mandatory_relations(
        self, mock_file, mock_path_wb, mock_path_chmod, subprocess, cclient, _utils, _cos_agent
    ):
        """Test the mandatory charm relations."""
        cclient.from_socket().cluster.list_services.return_value = []

        self.harness.set_leader()
        self.harness.update_config({"snap-channel": "1.0/stable"})
        self.add_complete_peer_relation(self.harness)

        subprocess.run.assert_any_call(
            [
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

        # Assert RGW update configs is not called
        cclient.from_socket().cluster.update_config.assert_not_called()

    @patch.object(ceph_cos_agent, "ceph_utils")
    @patch.object(microceph, "Client")
    @patch("utils.subprocess")
    @patch.object(Path, "chmod")
    @patch.object(Path, "write_bytes")
    @patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
    def test_all_relations(
        self, mock_file, mock_path_wb, mock_path_chmod, subprocess, cclient, _utils
    ):
        """Test all the charms relations."""
        cclient.from_socket().cluster.list_services.return_value = []

        self.harness.set_leader()
        self.harness.update_config({"snap-channel": "1.0/stable"})
        self.add_complete_peer_relation(self.harness)
        self.add_complete_identity_relation(self.harness)
        self.add_complete_ingress_relation(self.harness)
        self.add_complete_certificate_transfer_relation(self.harness)

        subprocess.run.assert_any_call(
            [
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

        # Assert RGW update configs is not called
        cclient.from_socket().cluster.update_config.assert_not_called()

    @patch.object(ceph_cos_agent, "ceph_utils")
    @patch("relation_handlers.Client", MagicMock())
    @patch.object(microceph, "Client")
    @patch("utils.subprocess")
    @patch.object(Path, "chmod")
    @patch.object(Path, "write_bytes")
    @patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
    def test_all_relations_with_enable_rgw_config(
        self, mock_file, mock_path_wb, mock_path_chmod, subprocess, cclient, _utils
    ):
        """Test all the charms relations with rgw enabled."""
        cclient.from_socket().cluster.list_services.return_value = []

        self.harness.set_leader()
        self.harness.update_config({"snap-channel": "1.0/stable", "enable-rgw": "*"})
        test_utils.add_complete_peer_relation(self.harness)
        self.add_complete_identity_relation(self.harness)
        self.add_complete_ingress_relation(self.harness)
        self.add_complete_certificate_transfer_relation(self.harness)

        subprocess.run.assert_any_call(
            [
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        subprocess.run.assert_any_call(
            [
                "microceph",
                "enable",
                "rgw",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

        # Check config rgw_swift_account_in_url is not updated since
        # namespace-projects is False by default.
        for mock_call in cclient.from_socket().cluster.update_config.mock_calls:
            assert mock_call.args[0] != "rgw_swift_account_in_url"

        # Check config rgw_keystone_verify_ssl is updated since certificate
        # transfer relation is set
        cclient.from_socket().cluster.update_config.assert_any_call(
            "rgw_keystone_verify_ssl", str(True).lower(), True
        )

    @patch.object(ceph_cos_agent, "ceph_utils")
    @patch("relation_handlers.Client", MagicMock())
    @patch.object(microceph, "Client")
    @patch("utils.subprocess")
    @patch.object(Path, "chmod")
    @patch.object(Path, "write_bytes")
    @patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
    def test_all_relations_with_enable_rgw_config_and_namespace_projects(
        self, mock_file, mock_path_wb, mock_path_chmod, subprocess, cclient, _utils
    ):
        """Test all the charms relations with rgw and namespace_projects enabled."""
        cclient.from_socket().cluster.list_services.return_value = []

        self.harness.set_leader()
        self.harness.update_config(
            {"snap-channel": "1.0/stable", "enable-rgw": "*", "namespace-projects": True}
        )
        test_utils.add_complete_peer_relation(self.harness)
        self.add_complete_identity_relation(self.harness)
        self.add_complete_ingress_relation(self.harness)
        self.add_complete_certificate_transfer_relation(self.harness)

        subprocess.run.assert_any_call(
            [
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        subprocess.run.assert_any_call(
            [
                "microceph",
                "enable",
                "rgw",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

        # Check config rgw_swift_account_in_url is updated since
        # namespace-projects is set to True.
        cclient.from_socket().cluster.update_config.assert_any_call(
            "rgw_swift_account_in_url", str(True).lower(), True
        )

        # Check config rgw_keystone_verify_ssl is updated since certificate
        # transfer relation is set
        cclient.from_socket().cluster.update_config.assert_any_call(
            "rgw_keystone_verify_ssl", str(True).lower(), True
        )

    @patch.object(ceph_cos_agent, "ceph_utils")
    @patch("relation_handlers.Client", MagicMock())
    @patch.object(microceph, "Client")
    @patch("utils.subprocess")
    @patch.object(Path, "chmod")
    @patch.object(Path, "write_bytes")
    @patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
    def test_relations_without_certificate_transfer(
        self, mock_file, mock_path_wb, mock_path_chmod, subprocess, cclient, _utils
    ):
        """Test all the charms relations without certificate transfer relation."""
        cclient.from_socket().cluster.list_services.return_value = []

        self.harness.set_leader()
        self.harness.update_config(
            {"snap-channel": "1.0/stable", "enable-rgw": "*", "namespace-projects": True}
        )
        test_utils.add_complete_peer_relation(self.harness)
        self.add_complete_identity_relation(self.harness)
        self.add_complete_ingress_relation(self.harness)
        subprocess.run.assert_any_call(
            [
                "microceph",
                "cluster",
                "bootstrap",
                "--public-network",
                "10.0.0.0/24",
                "--cluster-network",
                "10.0.0.0/24",
                "--microceph-ip",
                "10.0.0.10",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        subprocess.run.assert_any_call(
            [
                "microceph",
                "enable",
                "rgw",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

        # Check config rgw_swift_account_in_url is updated since
        # namespace-projects is set to True.
        cclient.from_socket().cluster.update_config.assert_any_call(
            "rgw_swift_account_in_url", str(True).lower(), True
        )

        # Check config rgw_keystone_verify_ssl is updated since certificate
        # transfer relation is set
        cclient.from_socket().cluster.update_config.assert_any_call(
            "rgw_keystone_verify_ssl", str(False).lower(), True
        )

    @patch("utils.subprocess")
    @patch("ceph.check_output")
    def test_add_osds_action_with_device_id(self, _chk, subprocess):
        """Test action add_osds."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness._charm.peers.interface.state.joined = True

        action_event = MagicMock()
        action_event.params = {"device-id": "/dev/sdb"}
        self.harness.charm.storage._add_osd_action(action_event)

        action_event.set_results.assert_called()
        action_event.fail.assert_not_called()
        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", "/dev/sdb"],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    @patch("utils.subprocess")
    @patch("ceph.check_output")
    def test_add_osds_action_with_already_added_device_id(self, _chk, subprocess):
        """Test action add_osds."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness._charm.peers.interface.state.joined = True

        disk = "/dev/sdb"
        error = 'Error: failed to record disk: This "disks" entry already exists\n'
        result = {"result": [{"spec": disk, "status": "failure", "message": error}]}
        subprocess.CalledProcessError = CalledProcessError
        subprocess.run.side_effect = CalledProcessError(returncode=1, cmd=["echo"], stderr=error)

        action_event = MagicMock()
        action_event.params = {"device-id": disk}
        self.harness.charm.storage._add_osd_action(action_event)

        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", disk],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        action_event.set_results.assert_called_with(result)
        action_event.fail.assert_called()

    @patch("utils.subprocess")
    @patch("ceph.check_output")
    def test_add_osds_action_with_loop_spec(self, _chk, subprocess):
        """Test action add_osds with loop file spec."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness._charm.peers.interface.state.joined = True

        action_event = MagicMock()
        action_event.params = {"loop-spec": "4G,3"}
        self.harness.charm.storage._add_osd_action(action_event)

        action_event.set_results.assert_called()
        action_event.fail.assert_not_called()
        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", "loop,4G,3"],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    def test_add_osds_action_node_not_bootstrapped(self):
        """Test action add_osds when node not bootstrapped."""
        test_utils.add_complete_peer_relation(self.harness)

        action_event = MagicMock()
        action_event.params = {"device-id": "/dev/sdb"}
        self.harness.charm.storage._add_osd_action(action_event)

        action_event.set_results.assert_called_with(
            {"message": "Node not yet joined in microceph cluster"}
        )
        action_event.fail.assert_called()

    def _create_subprocess_output_mock(self, stdout):
        _mock = MagicMock()
        self.subprocess.run.return_value = _mock
        type(_mock).stdout = PropertyMock(return_value=stdout)
        return _mock

    def _test_list_disks_action(self, microceph_cmd_output, expected_disks):
        """Test action list_disks."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness._charm.peers.interface.state.joined = True

        action_event = MagicMock()
        self._create_subprocess_output_mock(microceph_cmd_output)

        self.harness.charm.storage._list_disks_action(action_event)
        action_event.set_results.assert_called_with(expected_disks)

    def test_list_disks_action_node_not_bootstrapped(self):
        """Test action list_disks when node not bootstrapped."""
        test_utils.add_complete_peer_relation(self.harness)

        action_event = MagicMock()
        self.harness.charm.storage._list_disks_action(action_event)
        action_event.set_results.assert_called_with(
            {"message": "Node not yet joined in microceph cluster"}
        )
        action_event.fail.assert_called()

    @patch("utils.subprocess")
    def test_list_disks_action_no_osds_no_disks(self, subprocess):
        self.subprocess = subprocess
        microceph_cmd_output = '{"ConfiguredDisks":[],"AvailableDisks":[]}'

        expected_disks = {"osds": [], "unpartitioned-disks": []}
        self._test_list_disks_action(microceph_cmd_output, expected_disks)

    @patch("utils.subprocess")
    def test_list_disks_action_no_osds_1_disk(self, subprocess):
        self.subprocess = subprocess
        microceph_cmd_output = """{
            "ConfiguredDisks":[],
            "AvailableDisks":[{
                    "model": "QEMU HARDDISK",
                    "size": "1.00GiB",
                    "type": "scsi",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
            }]
        }"""

        expected_disks = {
            "osds": [],
            "unpartitioned-disks": [
                {
                    "model": "QEMU HARDDISK",
                    "size": "1.00GiB",
                    "type": "scsi",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1",
                }
            ],
        }
        self._test_list_disks_action(microceph_cmd_output, expected_disks)

    @patch("utils.subprocess")
    def test_list_disks_action_1_osd_no_disks(self, subprocess):
        self.subprocess = subprocess
        microceph_cmd_output = """{
            "ConfiguredDisks":[{
                "osd":0,
                "location":"microceph-1",
                "path":"/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
            }],
            "AvailableDisks":[]
        }"""

        expected_disks = {
            "osds": [
                {
                    "osd": 0,
                    "location": "microceph-1",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1",
                }
            ],
            "unpartitioned-disks": [],
        }
        self._test_list_disks_action(microceph_cmd_output, expected_disks)

    @patch("utils.subprocess")
    def test_list_disks_action_1_osd_1_disk(self, subprocess):
        self.subprocess = subprocess
        microceph_cmd_output = """{
            "ConfiguredDisks":[{
                "osd":0,
                "location":"microceph-1",
                "path":"/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
            }],
            "AvailableDisks":[{
                    "model": "QEMU HARDDISK",
                    "size": "1.00GiB",
                    "type": "scsi",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--2"
            }]
        }"""

        expected_disks = {
            "osds": [
                {
                    "osd": 0,
                    "location": "microceph-1",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1",
                }
            ],
            "unpartitioned-disks": [
                {
                    "model": "QEMU HARDDISK",
                    "size": "1.00GiB",
                    "type": "scsi",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--2",
                }
            ],
        }
        self._test_list_disks_action(microceph_cmd_output, expected_disks)

    @patch("utils.subprocess")
    def test_list_disks_action_1_osd_no_disks_fqdn(self, subprocess):
        self.subprocess = subprocess
        microceph_cmd_output = """{
            "ConfiguredDisks":[{
                "osd":0,
                "location":"microceph-1.lxd",
                "path":"/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
            }],
            "AvailableDisks":[]
        }"""

        expected_disks = {
            "osds": [
                {
                    "osd": 0,
                    "location": "microceph-1.lxd",
                    "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1",
                }
            ],
            "unpartitioned-disks": [],
        }
        self._test_list_disks_action(microceph_cmd_output, expected_disks)

    @patch("requests.get")
    def test_get_snap_info(self, mock_get):
        # Sample mocked response data
        mock_response_data = {
            "name": "test-snap",
            "summary": "A test snap",
            # ... add more fields as needed
        }
        mock_response = MagicMock()
        # mock_response.raise_for_status.return_value = None  # Avoid raising exceptions
        mock_response.json.return_value = mock_response_data
        mock_get.return_value = mock_response

        result = microceph.get_snap_info("test-snap")

        self.assertEqual(result, mock_response_data)
        mock_get.assert_called_once_with(
            "https://api.snapcraft.io/v2/snaps/info/test-snap",
            headers={"Snap-Device-Series": "16"},
        )

    @patch("microceph.get_snap_info")
    def test_get_snap_tracks(self, mock_get_snap_info):
        # Simulate get_snap_info output
        mock_snap_info = {
            "channel-map": [
                {"channel": {"track": "quincy/stable"}},
                {"channel": {"track": "reef/beta"}},
                {"channel": {"track": "quincy/stable"}},
            ]
        }
        mock_get_snap_info.return_value = mock_snap_info

        # Execute the code under test
        result = microceph.get_snap_tracks("test-snap")

        # Expected Assertion
        self.assertEqual(sorted(result), ["quincy/stable", "reef/beta"])

    @patch("microceph.get_snap_tracks")
    def test_can_upgrade_snap_empty_new_version(self, mock_get_snap_tracks):
        mock_get_snap_tracks.return_value = {"quincy", "reef"}
        result = microceph.can_upgrade_snap("quincy", "")
        self.assertFalse(result)

    @patch("microceph.get_snap_tracks")
    def test_can_upgrade_snap_to_latest(self, mock_get_snap_tracks):
        mock_get_snap_tracks.return_value = {"quincy", "reef"}
        result = microceph.can_upgrade_snap("latest", "latest")
        self.assertTrue(result)

    @patch("microceph.get_snap_tracks")
    def test_can_upgrade_snap_invalid_track(self, mock_get_snap_tracks):
        mock_get_snap_tracks.return_value = {"quincy"}
        result = microceph.can_upgrade_snap("latest", "invalid")
        self.assertFalse(result)

    @patch("microceph.get_snap_tracks")
    def test_can_upgrade_major_version(self, mock_get_snap_tracks):
        mock_get_snap_tracks.return_value = {"quincy", "reef"}
        result = microceph.can_upgrade_snap("quincy", "reef")
        self.assertTrue(result)

    @patch("microceph.get_snap_tracks")
    def test_cannot_downgrade_major_version(self, mock_get_snap_tracks):
        mock_get_snap_tracks.return_value = {"quincy", "reef"}
        result = microceph.can_upgrade_snap("reef", "quincy")
        self.assertFalse(result)

    @patch("microceph.get_snap_tracks")
    def test_can_upgrade_to_same_track(self, mock_get_snap_tracks):
        mock_get_snap_tracks.return_value = {"reef", "squid"}
        result = microceph.can_upgrade_snap("reef", "reef")
        self.assertTrue(result)

    @patch("microceph.get_snap_tracks")
    def test_can_upgrade_future(self, mock_get_snap_tracks):
        # hypothetical future releases
        mock_get_snap_tracks.return_value = {"zoidberg", "alphaville", "pyjama"}
        result = microceph.can_upgrade_snap("squid", "pyjama")
        self.assertTrue(result)

    def test_get_rgw_endpoints_action_node_not_bootstrapped(self):
        """Test action get_rgw_endpoints when node not bootstrapped."""
        test_utils.add_complete_peer_relation(self.harness)

        action_event = MagicMock()
        self.harness.charm.rgw._get_rgw_endpoints_action(action_event)
        action_event.set_results.assert_called_with(
            {"message": "Rados gateway endpoints are not set yet"}
        )
        action_event.fail.assert_called()

    @patch.object(ceph_cos_agent, "ceph_utils")
    @patch("relation_handlers.Client", MagicMock())
    @patch.object(microceph, "Client")
    @patch("utils.subprocess")
    @patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
    def test_get_rgw_endpoints_action_after_traefik_is_integrated(
        self, mock_file, subprocess, cclient, _utils
    ):
        """Test action get_rgw_endpoints after traefik is integrated."""
        cclient.from_socket().cluster.list_services.return_value = []
        self.harness.set_leader()
        self.harness.update_config(
            {"snap-channel": "1.0/stable", "enable-rgw": "*", "namespace-projects": True}
        )
        test_utils.add_complete_peer_relation(self.harness)
        self.add_complete_identity_relation(self.harness)
        self.add_complete_ingress_relation(self.harness)

        action_event = MagicMock()
        self.harness.charm.rgw._get_rgw_endpoints_action(action_event)
        expected_endpoints = {
            "swift": "http://dummy-ip/swift/v1/AUTH_$(project_id)s",
            "s3": "http://dummy-ip",
        }
        action_event.set_results.assert_called_with(expected_endpoints)
        action_event.fail.assert_not_called()

    @patch("charm.microceph_client.Client")
    def test_enter_maintenance_action_success(self, cclient):
        cclient.from_socket().cluster.enter_maintenance_mode.return_value = {
            "metadata": [
                {
                    "name": "A-ops",
                    "error": "",
                    "action": "description of A-ops",
                },
                {
                    "name": "B-ops",
                    "error": "",
                    "action": "description of B-ops",
                },
            ],
        }
        action_event = MagicMock()
        action_event.params = {
            "force": False,
            "dry-run": False,
            "set-noout": True,
            "stop-osds": False,
            "check-only": False,
            "ignore-check": False,
        }

        self.harness.charm.maintenance._enter_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {
                "actions": {
                    "step-1": {
                        "id": "A-ops",
                        "error": "",
                        "description": "description of A-ops",
                    },
                    "step-2": {
                        "id": "B-ops",
                        "error": "",
                        "description": "description of B-ops",
                    },
                },
                "errors": "",
                "status": "success",
            }
        )
        action_event.fail.assert_not_called()

    @patch("charm.microceph_client.Client")
    def test_enter_maintenance_action_failure(self, cclient):
        mock_enter = cclient.from_socket().cluster.enter_maintenance_mode
        mock_enter.side_effect = MaintenanceOperationFailedException(
            "some errors",
            {
                "metadata": [
                    {
                        "name": "A-ops",
                        "error": "some error",
                        "action": "description of A-ops",
                    },
                    {
                        "name": "B-ops",
                        "error": "some error",
                        "action": "description of B-ops",
                    },
                ],
            },
        )
        action_event = MagicMock()
        action_event.params = {
            "force": False,
            "dry-run": False,
            "set-noout": True,
            "stop-osds": False,
            "check-only": False,
            "ignore-check": False,
        }

        self.harness.charm.maintenance._enter_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {
                "actions": {
                    "step-1": {
                        "id": "A-ops",
                        "error": "some error",
                        "description": "description of A-ops",
                    },
                    "step-2": {
                        "id": "B-ops",
                        "error": "some error",
                        "description": "description of B-ops",
                    },
                },
                "errors": "some errors",
                "status": "failure",
            }
        )
        action_event.fail.assert_called()

    @patch("charm.microceph_client.Client")
    def test_enter_maintenance_action_error(self, cclient):
        cclient.from_socket().cluster.enter_maintenance_mode.side_effect = Exception("some errors")
        action_event = MagicMock()
        action_event.params = {
            "force": False,
            "dry-run": False,
            "set-noout": True,
            "stop-osds": False,
            "check-only": False,
            "ignore-check": False,
        }

        self.harness.charm.maintenance._enter_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {"status": "failure", "errors": "some errors", "actions": {}}
        )
        action_event.fail.assert_called()

    @patch("charm.microceph_client.Client")
    def test_enter_maintenance_action_mutually_exclusive(self, cclient):
        action_event = MagicMock()
        action_event.params = {"check-only": True, "ignore-check": True}

        self.harness.charm.maintenance._enter_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {
                "status": "failure",
                "errors": "check-only and ignore-check cannot be used together",
                "actions": {},
            }
        )
        action_event.fail.assert_called()

    @patch("charm.microceph_client.Client")
    def test_exit_maintenance_action_success(self, cclient):
        cclient.from_socket().cluster.exit_maintenance_mode.return_value = {
            "metadata": [
                {
                    "name": "A-ops",
                    "error": "",
                    "action": "description of A-ops",
                },
                {
                    "name": "B-ops",
                    "error": "",
                    "action": "description of B-ops",
                },
            ],
        }
        action_event = MagicMock()
        action_event.params = {"dry-run": False}

        self.harness.charm.maintenance._exit_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {
                "actions": {
                    "step-1": {
                        "id": "A-ops",
                        "error": "",
                        "description": "description of A-ops",
                    },
                    "step-2": {
                        "id": "B-ops",
                        "error": "",
                        "description": "description of B-ops",
                    },
                },
                "errors": "",
                "status": "success",
            }
        )
        action_event.fail.assert_not_called()

    @patch("charm.microceph_client.Client")
    def test_exit_maintenance_action_failure(self, cclient):
        mock_exit = cclient.from_socket().cluster.exit_maintenance_mode
        mock_exit.side_effect = MaintenanceOperationFailedException(
            "some errors",
            {
                "metadata": [
                    {
                        "name": "A-ops",
                        "error": "some error",
                        "action": "description of A-ops",
                    },
                    {
                        "name": "B-ops",
                        "error": "some error",
                        "action": "description of B-ops",
                    },
                ],
            },
        )
        action_event = MagicMock()
        action_event.params = {"dry-run": False}

        self.harness.charm.maintenance._exit_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {
                "actions": {
                    "step-1": {
                        "id": "A-ops",
                        "error": "some error",
                        "description": "description of A-ops",
                    },
                    "step-2": {
                        "id": "B-ops",
                        "error": "some error",
                        "description": "description of B-ops",
                    },
                },
                "errors": "some errors",
                "status": "failure",
            }
        )
        action_event.fail.assert_called()

    @patch("charm.microceph_client.Client")
    def test_exit_maintenance_action_error(self, cclient):
        cclient.from_socket().cluster.exit_maintenance_mode.side_effect = Exception("some errors")
        action_event = MagicMock()
        action_event.params = {"dry-run": False}

        self.harness.charm.maintenance._exit_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {"status": "failure", "errors": "some errors", "actions": {}}
        )
        action_event.fail.assert_called()

    @patch("charm.microceph_client.Client")
    def test_exit_maintenance_action_mutually_exclusive(self, cclient):
        action_event = MagicMock()
        action_event.params = {"check-only": True, "ignore-check": True}

        self.harness.charm.maintenance._exit_maintenance_action(action_event)
        action_event.set_results.assert_called_with(
            {
                "status": "failure",
                "errors": "check-only and ignore-check cannot be used together",
                "actions": {},
            }
        )
        action_event.fail.assert_called()

    @patch("microceph.is_ready")
    @patch("ceph.enable_mgr_module")
    @patch("utils.subprocess")
    @patch.object(ceph_cos_agent, "ceph_utils")
    def test_cos_integration(self, ceph_utils, _sub, enable_mgr_module, is_ready):
        """Test integration for COS agent."""
        is_ready.return_value = True
        self.harness.set_leader()
        self.harness.update_config({"rbd-stats-pools": "abcd", "enable-perf-metrics": True})

        self.add_cos_agent_integration(self.harness)
        enable_mgr_module.assert_called_once_with("prometheus")
        ceph_utils.mgr_config_set.assert_has_calls(
            [
                call("mgr/prometheus/rbd_stats_pools", "abcd"),
                call("mgr/prometheus/exclude_perf_counters", "False"),
            ]
        )
