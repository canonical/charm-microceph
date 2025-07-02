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
from unit import testbase

import relation_handlers


class TestRelationHelpers(testbase.TestBaseCharm):
    PATCHES = [
        "gethostname",
    ]

    def setUp(self):
        """Setup MicroCeph Charm tests."""
        super().setUp(relation_handlers, self.PATCHES)
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

    def test_collect_peer_data(self):
        self.harness.set_leader()
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        # set up some initial relation data
        self.harness.update_relation_data(
            rel_id,
            "microceph/0",
            {
                unit_name: "test-hostname",
            },
        )
        self.gethostname.return_value = "test-hostname"
        change_data = relation_handlers.collect_peer_data(self.harness.model)
        self.assertNotIn(unit_name, change_data)
        self.assertEqual(change_data["public-address"], "10.0.0.10")
        self.gethostname.return_value = "changed-hostname"
        # assert that collect_peer_data raises an exception
        with self.assertRaises(relation_handlers.HostnameChangeError):
            relation_handlers.collect_peer_data(self.harness.model)


class TestCephNfsClientProvides(testbase.TestBaseCharm):

    def setUp(self):
        """Setup MicroCeph Charm tests."""
        super().setUp(relation_handlers, [])
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
            ("broker_check_output", "ceph_broker.check_output"),
            ("microceph_check_output", "microceph.subprocess.check_output"),
            ("_run_cmd", "microceph._run_cmd"),
            ("create_fs_volume", "microceph.create_fs_volume"),
            ("enable_nfs", "microceph.enable_nfs"),
            ("disable_nfs", "microceph.disable_nfs"),
            ("get_mon_addresses", "microceph_client.ClusterService.get_mon_addresses"),
            ("list_services", "microceph_client.ClusterService.list_services"),
            ("get_osd_count", "relation_handlers.get_osd_count"),
            ("get_named_key", "relation_handlers.get_named_key"),
            ("remove_named_key", "relation_handlers.remove_named_key"),
        ]

        for attr_name, thing in patch_list:
            patcher = patch(thing)
            mock_obj = patcher.start()
            setattr(self, attr_name, mock_obj)
            self.addCleanup(patcher.stop)

        self.get_named_key.return_value = "fake-key"
        self.list_services.return_value = []

        def _add_service(candidate, cluster_id):
            self.list_services.return_value.append(
                {"service": f"nfs.{cluster_id}", "location": candidate}
            )

        self.enable_nfs.side_effect = _add_service

        def _remove_service(candidate, cluster_id):
            for svc in self.list_services.return_value:
                if svc["service"] == f"nfs.{cluster_id}" and svc["location"] == candidate:
                    self.list_services.return_value.remove(svc)
                    return

        self.disable_nfs.side_effect = _remove_service

    def test_ceph_nfs_connected_not_emitted(self):
        self.ready_for_service.return_value = False
        self.get_osd_count.return_value = 0

        self.harness.set_leader()
        self.add_ceph_nfs_relation(self.harness)

        self.get_osd_count.assert_not_called()

        self.ready_for_service.return_value = True

        self.add_ceph_nfs_relation(self.harness)

        # _on_relation_changed is called on relation join and changed events.
        self.get_osd_count.assert_has_calls([call()] * 2)

    def test_ensure_nfs_cluster(self):
        # No nodes.
        self.list_services.return_value = []
        self.get_mon_addresses.return_value = ["foo.lish"]
        self.harness.set_leader()

        self.add_ceph_nfs_relation(self.harness)

        self.enable_nfs.assert_not_called()

        # Add a node, the NFS cluster should extend to it.
        self.list_services.return_value = [
            {"service": "mon", "location": "foo1"},
            {"service": "mgr", "location": "foo1"},
            {"service": "osd", "location": "foo1"},
            {"service": "mds", "location": "foo1"},
        ]

        rel_id = test_utils.add_complete_peer_relation(self.harness)

        self.enable_nfs.assert_called_once_with("foo1", "manila-cephfs")
        self.create_fs_volume.assert_called_once_with("manila-cephfs-vol")
        caps = {"mon": ["allow r"], "mgr": ["allow rw"]}
        self.get_named_key.assert_called_once_with("client.manila-cephfs", caps)

        # Add 2 more nodes, the NFS cluster should only enabled on the 3 nodes.
        self.list_services.return_value += [
            {"service": "mon", "location": "foo2"},
        ]
        self.add_unit(self.harness, rel_id, "microceph/2", {"foo": "lish"})

        self.list_services.return_value += [
            {"service": "mon", "location": "foo3"},
        ]
        self.add_unit(self.harness, rel_id, "microceph/3", {"foo": "lish"})

        self.enable_nfs.assert_has_calls(
            [
                call("foo1", "manila-cephfs"),
                call("foo2", "manila-cephfs"),
                call("foo3", "manila-cephfs"),
            ]
        )

        # Add a 4th node, enable_nfs should not be called again.
        self.enable_nfs.reset_mock()
        self.list_services.return_value += [
            {"service": "mon", "location": "foo4"},
        ]
        self.add_unit(self.harness, rel_id, "microceph/4", {"foo": "lish"})

        self.enable_nfs.assert_not_called()

        # Add a new relation, should use the 4th node.
        self.add_ceph_nfs_relation(self.harness, "another-app")

        self.enable_nfs.assert_called_with("foo4", "another-app")

        # Add another relation, but this time there's no available node.
        self.enable_nfs.reset_mock()

        self.add_ceph_nfs_relation(self.harness, "yet-another-app")

        self.enable_nfs.assert_not_called()

    def test_peers_updated_rel_data(self):
        self.get_mon_addresses.return_value = []
        self.list_services.return_value = []
        self.harness.set_leader()

        rel_id = self.add_ceph_nfs_relation(self.harness)

        rel_data = self.harness.get_relation_data(rel_id, self.harness.model.app)
        self.assertIsNone(rel_data.get("mon-hosts"))

        # Add a peer unit. mon-hosts should be updated.
        self.get_mon_addresses.return_value = ["foo.lish"]

        test_utils.add_complete_peer_relation(self.harness)

        self.assertEqual('["foo.lish"]', rel_data.get("mon-hosts"))

    def test_remove_relation_rebalance(self):
        self.get_mon_addresses.return_value = ["foo.lish"]
        self.harness.set_leader()
        test_utils.add_complete_peer_relation(self.harness)
        self.list_services.return_value = [
            {"service": "mon", "location": "foo1"},
            {"service": "mon", "location": "foo2"},
            {"service": "mon", "location": "foo3"},
        ]

        # Add the first ceph-nfs relation. It should use up all 3 available nodes.
        rel_id = self.add_ceph_nfs_relation(self.harness)

        self.enable_nfs.assert_has_calls(
            [
                call("foo1", "manila-cephfs"),
                call("foo2", "manila-cephfs"),
                call("foo3", "manila-cephfs"),
            ],
            any_order=True,
        )

        # Add a new relation, it should not have any node available.
        self.enable_nfs.reset_mock()
        self.create_fs_volume.reset_mock()
        self.get_named_key.reset_mock()

        self.add_ceph_nfs_relation(self.harness, "another-app")

        self.enable_nfs.assert_not_called()
        self.create_fs_volume.assert_not_called()
        self.get_named_key.assert_not_called()

        # Remove first relation, the second one should now use the nodes.
        self.harness.remove_relation(rel_id)

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
                call("foo1", "another-app"),
                call("foo2", "another-app"),
                call("foo3", "another-app"),
            ],
            any_order=True,
        )
        self.create_fs_volume.assert_called_once_with("another-app-vol")
        caps = {"mon": ["allow r"], "mgr": ["allow rw"]}
        self.get_named_key.assert_called_once_with("client.another-app", caps)


if __name__ == "__main__":
    unittest.main()
