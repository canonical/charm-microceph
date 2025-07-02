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

import unittest
from unittest.mock import call, patch

import ops_sunbeam.test_utils as test_utils
from ops.model import ActiveStatus, BlockedStatus
from unit import testbase

import ceph_nfs


class TestCephNfsClientProvides(testbase.TestBaseCharm):

    def setUp(self):
        """Setup MicroCeph Charm tests."""
        super().setUp(ceph_nfs, [])
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

        patcher = patch.object(self.harness.charm, "ready_for_service")
        self.ready_for_service = patcher.start()
        self.addCleanup(patcher.stop)

        patch_list = [
            # (self.attr_name, thing_to_patch)
            ("ceph_check_output", "ceph.check_output"),
            ("create_fs_volume", "ceph.create_fs_volume"),
            ("get_named_key", "ceph.get_named_key"),
            ("remove_named_key", "ceph.remove_named_key"),
            ("get_osd_count", "ceph.get_osd_count"),
            ("list_fs_volumes", "ceph.list_fs_volumes"),
            ("broker_check_output", "ceph_broker.check_output"),
            ("enable_nfs", "microceph.enable_nfs"),
            ("disable_nfs", "microceph.disable_nfs"),
            ("microceph_has_service", "microceph.microceph_has_service"),
            ("microceph_check_output", "microceph.subprocess.check_output"),
            ("list_services", "microceph_client.ClusterService.list_services"),
            ("get_fsid", "utils.get_fsid"),
            ("get_mon_addresses", "utils.get_mon_addresses"),
            ("run_cmd", "utils.run_cmd"),
        ]

        for attr_name, thing in patch_list:
            patcher = patch(thing)
            mock_obj = patcher.start()
            setattr(self, attr_name, mock_obj)
            self.addCleanup(patcher.stop)

        self.get_named_key.return_value = "fa-key"
        self.list_services.return_value = []
        self.list_fs_volumes.return_value = []
        self.get_fsid.return_value = "f00"
        self.get_mon_addresses.return_value = ["foo.lish"]

        def _add_service(candidate, cluster_id, _):
            self.list_services.return_value.append(
                {"service": "nfs", "group_id": cluster_id, "location": candidate}
            )

        self.enable_nfs.side_effect = _add_service

        def _remove_service(candidate, cluster_id):
            for svc in self.list_services.return_value:
                if (
                    svc["service"] == "nfs"
                    and svc["group_id"] == cluster_id
                    and svc["location"] == candidate
                ):
                    self.list_services.return_value.remove(svc)
                    return

        self.disable_nfs.side_effect = _remove_service

        def _add_fs_volume(volume_name):
            self.list_fs_volumes.return_value.append(
                {
                    "name": volume_name,
                }
            )

        self.create_fs_volume.side_effect = _add_fs_volume

    def _add_peer_unit(self, rel_id, number):
        host = f"foo{number}"
        unit_name = f"microceph/{number}"

        self.list_services.return_value += [
            {"service": "mon", "location": host},
        ]

        unit_data = {
            "public-address": f"pub-addr-{number}",
            unit_name: f"foo{number}",
        }
        self.add_unit(self.harness, rel_id, unit_name, unit_data)

    def test_ceph_nfs_connected_not_emitted(self):
        self.ready_for_service.return_value = False
        self.get_osd_count.return_value = 0

        self.harness.set_leader()
        self.add_ceph_nfs_relation(self.harness)

        # charm is not ready for service, and thus the relation doesn't change
        # its status yet.
        ceph_nfs_status = self.harness.charm.ceph_nfs.status
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)
        self.get_osd_count.assert_not_called()

        # The charm is now ready to service, but has no OSDs.
        self.ready_for_service.return_value = True

        self.add_ceph_nfs_relation(self.harness)

        # _on_relation_changed is called on relation join and changed events.
        self.get_osd_count.assert_has_calls([call()] * 2)

    def test_ceph_nfs_servicability(self):
        self.microceph_has_service.return_value = False
        self.harness.set_leader()
        ceph_nfs = self.harness.charm.ceph_nfs

        ceph_nfs.set_status(ceph_nfs.status)

        # No ceph-nfs relations, the status should be Active.
        self.assertIsInstance(ceph_nfs.status.status, ActiveStatus)
        self.microceph_has_service.assert_not_called()

        self.add_ceph_nfs_relation(self.harness)
        ceph_nfs.set_status(ceph_nfs.status)

        # Has ceph-nfs relation, but has no NFS support.
        self.assertIsInstance(ceph_nfs.status.status, BlockedStatus)
        self.microceph_has_service.assert_called_once()
        self.enable_nfs.assert_not_called()

        # # The snap now has NFS support.
        self.microceph_has_service.reset_mock()
        self.microceph_has_service.return_value = True
        unit_data = {
            "public-address": "pub-addr-1",
            "microceph/1": "foo1",
        }

        ceph_nfs.set_status(ceph_nfs.status)

        self.assertIsInstance(ceph_nfs.status.status, ActiveStatus)

        rel_id = self.add_complete_peer_relation(self.harness, unit_data)
        self._add_peer_unit(rel_id, 1)

        self.microceph_has_service.assert_called_once()
        self.enable_nfs.assert_called_once()
        self.assertIsInstance(ceph_nfs.status.status, ActiveStatus)

    def test_ensure_nfs_cluster(self):
        # No nodes.
        self.list_services.return_value = []
        self.harness.set_leader()

        nfs_rel_id = self.add_ceph_nfs_relation(self.harness)

        self.enable_nfs.assert_not_called()
        self.create_fs_volume.assert_not_called()

        ceph_nfs_status = self.harness.charm.ceph_nfs.status
        self.assertIsInstance(ceph_nfs_status.status, BlockedStatus)

        # Add a node, the NFS cluster should extend to it.
        self.list_services.return_value = [
            {"service": "mon", "location": "foo1"},
            {"service": "mgr", "location": "foo1"},
            {"service": "osd", "location": "foo1"},
            {"service": "mds", "location": "foo1"},
        ]

        unit_data = {
            "public-address": "pub-addr-1",
            "microceph/1": "foo1",
        }
        rel_id = self.add_complete_peer_relation(self.harness, unit_data)
        self._add_peer_unit(rel_id, 1)

        self.enable_nfs.assert_called_once_with("foo1", "manila-cephfs", "pub-addr-1")
        self.create_fs_volume.assert_called_once_with("manila-cephfs-vol")
        caps = {"mon": ["allow r"], "mgr": ["allow rw"]}
        self.get_named_key.assert_called_once_with("client.manila-cephfs", caps)

        rel_data = self.harness.get_relation_data(nfs_rel_id, self.harness.model.app)
        expected_data = {
            "client": "client.manila-cephfs",
            "keyring": "fa-key",
            "mon_hosts": '["foo.lish"]',
            "cluster-id": "manila-cephfs",
            "volume": "manila-cephfs-vol",
            "fsid": "f00",
        }
        self.assertEqual(expected_data, rel_data)
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)

        # Add 2 more nodes, the NFS cluster should only enabled on the 3 nodes.
        self.create_fs_volume.reset_mock()
        self._add_peer_unit(rel_id, 2)
        self._add_peer_unit(rel_id, 3)

        self.enable_nfs.assert_has_calls(
            [
                call("foo1", "manila-cephfs", "pub-addr-1"),
                call("foo2", "manila-cephfs", "pub-addr-2"),
                call("foo3", "manila-cephfs", "pub-addr-3"),
            ]
        )
        self.create_fs_volume.assert_not_called()
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)

        # Add a 4th node, enable_nfs should not be called again.
        self.enable_nfs.reset_mock()
        self._add_peer_unit(rel_id, 4)

        self.enable_nfs.assert_not_called()
        self.create_fs_volume.assert_not_called()
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)

        # Add a new relation, should use the 4th node.
        another_nfs_rel_id = self.add_ceph_nfs_relation(self.harness, "another-app")

        self.enable_nfs.assert_called_with("foo4", "another-app", "pub-addr-4")
        self.create_fs_volume.assert_called_once_with("another-app-vol")
        rel_data = self.harness.get_relation_data(another_nfs_rel_id, self.harness.model.app)
        expected_data = {
            "client": "client.another-app",
            "keyring": "fa-key",
            "mon_hosts": '["foo.lish"]',
            "cluster-id": "another-app",
            "volume": "another-app-vol",
            "fsid": "f00",
        }
        self.assertEqual(expected_data, rel_data)
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)

        # Add another relation, but this time there's no available node.
        self.enable_nfs.reset_mock()
        self.create_fs_volume.reset_mock()

        self.add_ceph_nfs_relation(self.harness, "yet-another-app")

        self.enable_nfs.assert_not_called()
        self.create_fs_volume.assert_not_called()
        self.assertIsInstance(ceph_nfs_status.status, BlockedStatus)

    def test_peers_updated_rel_data(self):
        self.get_mon_addresses.return_value = []
        self.list_services.return_value = []
        self.harness.set_leader()

        rel_id = self.add_ceph_nfs_relation(self.harness)

        rel_data = self.harness.get_relation_data(rel_id, self.harness.model.app)
        self.assertIsNone(rel_data.get("mon_hosts"))

        # Add a peer unit. mon-hosts should be updated.
        self.get_mon_addresses.return_value = ["foo.lish"]

        unit_data = {
            "public-address": "pub-addr-1",
            "microceph/1": "foo1",
        }
        rel_id = self.add_complete_peer_relation(self.harness, unit_data)
        self._add_peer_unit(rel_id, 1)

        self.assertEqual('["foo.lish"]', rel_data.get("mon_hosts"))

    def test_relation_no_longer_servicable(self):
        self.harness.set_leader()
        unit_data = {
            "public-address": "pub-addr-1",
            "microceph/1": "foo1",
        }
        rel_id = self.add_complete_peer_relation(self.harness, unit_data)
        self._add_peer_unit(rel_id, 1)

        # Add the ceph-nfs relation. It should use the available node.
        nfs_rel_id = self.add_ceph_nfs_relation(self.harness)

        self.enable_nfs.assert_called_once_with("foo1", "manila-cephfs", "pub-addr-1")
        self.create_fs_volume.assert_called_once_with("manila-cephfs-vol")
        rel_data = self.harness.get_relation_data(nfs_rel_id, self.harness.model.app)
        expected_data = {
            "client": "client.manila-cephfs",
            "keyring": "fa-key",
            "mon_hosts": '["foo.lish"]',
            "cluster-id": "manila-cephfs",
            "volume": "manila-cephfs-vol",
            "fsid": "f00",
        }
        self.assertEqual(expected_data, rel_data)
        ceph_nfs_status = self.harness.charm.ceph_nfs.status
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)

        # Remove the only microceph unit, the relation data should be cleared,
        # as there is no node available to service it anymore.
        self.list_services.return_value = []
        self.harness.remove_relation_unit(rel_id, "microceph/1")

        self.assertEqual({}, rel_data)
        self.assertIsInstance(ceph_nfs_status.status, BlockedStatus)

    def test_remove_relation_rebalance(self):
        self.harness.set_leader()
        unit_data = {
            "public-address": "pub-addr-1",
            "microceph/1": "foo1",
        }
        rel_id = self.add_complete_peer_relation(self.harness, unit_data)
        self._add_peer_unit(rel_id, 1)
        self._add_peer_unit(rel_id, 2)
        self._add_peer_unit(rel_id, 3)

        # Add the first ceph-nfs relation. It should use up all 3 available nodes.
        ceph_rel_id = self.add_ceph_nfs_relation(self.harness)
        self._add_peer_unit(ceph_rel_id, 1)

        self.enable_nfs.assert_has_calls(
            [
                call("foo1", "manila-cephfs", "pub-addr-1"),
                call("foo2", "manila-cephfs", "pub-addr-2"),
                call("foo3", "manila-cephfs", "pub-addr-3"),
            ],
            any_order=True,
        )
        ceph_nfs_status = self.harness.charm.ceph_nfs.status
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)

        # Add a new relation, it should not have any node available.
        self.enable_nfs.reset_mock()
        self.create_fs_volume.reset_mock()
        self.get_named_key.reset_mock()

        self.add_ceph_nfs_relation(self.harness, "another-app")

        self.enable_nfs.assert_not_called()
        self.create_fs_volume.assert_not_called()
        self.get_named_key.assert_not_called()
        self.assertIsInstance(ceph_nfs_status.status, BlockedStatus)

        # Remove first relation, the second one should now use the nodes.
        self.harness.remove_relation(ceph_rel_id)

        self.disable_nfs.assert_has_calls(
            [
                call("foo1", "manila-cephfs"),
                call("foo2", "manila-cephfs"),
                call("foo3", "manila-cephfs"),
            ],
            any_order=True,
        )
        self.remove_named_key.assert_called_once_with("client.manila-cephfs")
        self.enable_nfs.assert_has_calls(
            [
                call("foo1", "another-app", "pub-addr-1"),
                call("foo2", "another-app", "pub-addr-2"),
                call("foo3", "another-app", "pub-addr-3"),
            ],
            any_order=True,
        )
        self.create_fs_volume.assert_called_once_with("another-app-vol")
        caps = {"mon": ["allow r"], "mgr": ["allow rw"]}
        self.get_named_key.assert_called_once_with("client.another-app", caps)
        self.assertIsInstance(ceph_nfs_status.status, ActiveStatus)


if __name__ == "__main__":
    unittest.main()
