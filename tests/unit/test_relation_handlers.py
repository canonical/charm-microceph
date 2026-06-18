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
from unittest.mock import MagicMock, patch

import ops
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

    def test_collect_peer_data_no_nfs_address_by_default(self):
        # nfs-use-dedicated-binding defaults to false: NFS stays on the public
        # address and no nfs-address is published.
        self.harness.set_leader()
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        self.harness.update_relation_data(rel_id, "microceph/0", {unit_name: "test-hostname"})
        self.gethostname.return_value = "test-hostname"

        change_data = relation_handlers.collect_peer_data(self.harness.model)

        self.assertNotIn("nfs-address", change_data)

    def test_collect_peer_data_clears_stale_nfs_address_when_disabled(self):
        # With the option disabled, a previously published nfs-address is cleared
        # so the NFS provider reverts to the public address.
        self.harness.set_leader()
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        self.harness.update_relation_data(
            rel_id, unit_name, {unit_name: "test-hostname", "nfs-address": "10.20.0.10"}
        )
        self.gethostname.return_value = "test-hostname"

        change_data = relation_handlers.collect_peer_data(self.harness.model)

        self.assertEqual(change_data["nfs-address"], "")

    def test_collect_peer_data_publishes_nfs_address(self):
        # With nfs-use-dedicated-binding enabled, NFS binds to the ceph-nfs
        # endpoint's space and that address is published as nfs-address. The
        # harness resolves every binding to 10.0.0.10.
        self.harness.set_leader()
        self.harness.disable_hooks()
        self.harness.update_config({"nfs-use-dedicated-binding": True})
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        self.harness.update_relation_data(rel_id, "microceph/0", {unit_name: "test-hostname"})
        self.gethostname.return_value = "test-hostname"

        change_data = relation_handlers.collect_peer_data(self.harness.model)

        self.assertEqual(change_data["nfs-address"], "10.0.0.10")

    def test_collect_peer_data_resolves_from_ceph_nfs_binding(self):
        # When enabled, nfs-address is sourced from the ceph-nfs endpoint
        # binding: the address on that binding is what gets published.
        self.harness.set_leader()
        self.harness.disable_hooks()
        self.harness.update_config({"nfs-use-dedicated-binding": True})
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        self.harness.update_relation_data(rel_id, "microceph/0", {unit_name: "test-hostname"})
        self.gethostname.return_value = "test-hostname"

        def fake_get_binding(binding_key):
            # ceph-nfs resolves to a distinct address; others keep the default.
            binding = MagicMock()
            binding.network.bind_address = (
                "10.20.0.10" if binding_key == relation_handlers.NFS_BINDING else "10.0.0.10"
            )
            return binding

        with patch.object(
            self.harness.model, "get_binding", side_effect=fake_get_binding
        ) as get_binding:
            change_data = relation_handlers.collect_peer_data(self.harness.model)

        get_binding.assert_any_call(binding_key=relation_handlers.NFS_BINDING)
        self.assertEqual(change_data["nfs-address"], "10.20.0.10")
        self.assertEqual(change_data["public-address"], "10.0.0.10")

    def test_collect_peer_data_skips_nfs_address_on_binding_error(self):
        # Enabled but the ceph-nfs binding cannot be resolved and none was
        # published before: nothing is published, so NFS uses the public address.
        self.harness.set_leader()
        self.harness.disable_hooks()
        self.harness.update_config({"nfs-use-dedicated-binding": True})
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        self.harness.update_relation_data(rel_id, "microceph/0", {unit_name: "test-hostname"})
        self.gethostname.return_value = "test-hostname"

        def fake_get_binding(binding_key):
            if binding_key == relation_handlers.NFS_BINDING:
                raise ops.model.ModelError("boom")
            binding = MagicMock()
            binding.network.bind_address = "10.0.0.10"
            return binding

        with patch.object(self.harness.model, "get_binding", side_effect=fake_get_binding):
            change_data = relation_handlers.collect_peer_data(self.harness.model)

        self.assertNotIn("nfs-address", change_data)
        self.assertEqual(change_data["public-address"], "10.0.0.10")

    def test_collect_peer_data_clears_stale_nfs_address_on_binding_error(self):
        # Enabled but the ceph-nfs binding cannot be resolved: a previously
        # published nfs-address is cleared, so NFS falls back to the public
        # address (an unresolvable binding is treated like the option being off).
        self.harness.set_leader()
        self.harness.disable_hooks()
        self.harness.update_config({"nfs-use-dedicated-binding": True})
        rel_id = self.add_complete_peer_relation(self.harness)
        unit_name = self.harness.model.unit.name
        self.harness.update_relation_data(
            rel_id, unit_name, {unit_name: "test-hostname", "nfs-address": "10.20.0.10"}
        )
        self.gethostname.return_value = "test-hostname"

        def fake_get_binding(binding_key):
            if binding_key == relation_handlers.NFS_BINDING:
                raise ops.model.ModelError("boom")
            binding = MagicMock()
            binding.network.bind_address = "10.0.0.10"
            return binding

        with patch.object(self.harness.model, "get_binding", side_effect=fake_get_binding):
            change_data = relation_handlers.collect_peer_data(self.harness.model)

        self.assertEqual(change_data["nfs-address"], "")
        self.assertEqual(change_data["public-address"], "10.0.0.10")


if __name__ == "__main__":
    unittest.main()
