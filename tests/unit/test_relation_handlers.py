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

import json
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


class TestCephClientProvides(testbase.TestBaseCharm):
    """Regression tests for mon-address publishing to ceph clients.

    Reproduces the bug where a client (e.g. cinder-volume) receives only a
    single mon host. The microceph charm must publish the full mon list and
    every unit's own address independently of Ceph mon leadership, otherwise
    clients only see the one unit that is both the Juju leader and the Ceph
    mon leader.
    """

    PATCHES: list = []

    # The full set of mons the cluster reports. The unit under test owns the
    # second address (see the _lookup_system_interfaces mock below).
    MON_ADDRS = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    SELF_ADDR = "10.0.0.2"

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

        # Service is up and has OSDs so _on_relation_changed proceeds.
        ready = patch.object(self.harness.charm, "ready_for_service", return_value=True)
        ready.start()
        self.addCleanup(ready.stop)

        # This unit's own public address resolved from the mon list.
        lookup = patch.object(
            self.harness.charm, "_lookup_system_interfaces", return_value=self.SELF_ADDR
        )
        lookup.start()
        self.addCleanup(lookup.stop)

        patch_list = [
            ("get_osd_count", "relation_handlers.get_osd_count"),
            ("is_ceph_mon_leader", "relation_handlers.is_ceph_mon_leader"),
            ("get_mon_addresses", "utils.get_mon_addresses"),
        ]
        for attr_name, thing in patch_list:
            patcher = patch(thing)
            mock_obj = patcher.start()
            setattr(self, attr_name, mock_obj)
            self.addCleanup(patcher.stop)

        self.get_osd_count.return_value = 3
        # The crux of the bug: this unit is NOT the Ceph mon leader.
        self.is_ceph_mon_leader.return_value = False
        self.get_mon_addresses.return_value = list(self.MON_ADDRS)

    def _add_ceph_client_relation(self, app="cinder-volume"):
        broker_req = json.dumps({"request-id": "req-1", "ops": []})
        return self.harness.add_relation("ceph", app, unit_data={"broker_req": broker_req})

    def test_publishes_full_mon_list_when_not_mon_leader(self):
        """Fix A: the Juju leader publishes the full mon list to app data.

        This must happen even though the unit is not the Ceph mon leader.
        """
        self.harness.set_leader(True)
        rel_id = self._add_ceph_client_relation()

        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertIn("ceph-mon-public-addresses", app_data)
        self.assertEqual(json.loads(app_data["ceph-mon-public-addresses"]), self.MON_ADDRS)

    def test_unit_publishes_own_address_when_not_mon_leader(self):
        """Fix B: every unit advertises its own ceph-public-address.

        Mirrors classic ceph-mon, so the client fallback also lists all mons.
        """
        self.harness.set_leader(True)
        rel_id = self._add_ceph_client_relation()

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertEqual(unit_data.get("ceph-public-address"), self.SELF_ADDR)

    def test_non_juju_leader_publishes_unit_addr_but_not_app_list(self):
        """A non-Juju-leader publishes its own address but never the app list.

        Only the Juju leader may write app data; a non-leader writing the app
        databag would raise. It still publishes its own per-unit address.
        """
        self.harness.set_leader(False)
        rel_id = self._add_ceph_client_relation()

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertEqual(unit_data.get("ceph-public-address"), self.SELF_ADDR)

        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertNotIn("ceph-mon-public-addresses", app_data)

    def test_mon_leader_without_juju_leadership_still_correct(self):
        """The other half of the Juju-leader != Ceph-mon-leader scenario.

        Here this unit IS the Ceph mon leader but is NOT the Juju leader. It
        must process the broker (deliver key/auth) and advertise its own
        address, but it must NOT own the app-level list - only the Juju leader
        writes that. Together with test_publishes_full_mon_list_when_not_mon_leader
        (the Juju-leader-but-not-mon-leader unit), this pins the full J != M case.
        """
        self.harness.set_leader(False)
        self.is_ceph_mon_leader.return_value = True
        with patch("relation_handlers.process_requests", return_value={"exit-code": 0}):
            with patch("ceph.get_named_key", return_value="a-key"):
                rel_id = self._add_ceph_client_relation()

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        # Advertises its own monitor address (Fix B) and delivers the credentials.
        self.assertEqual(unit_data.get("ceph-public-address"), self.SELF_ADDR)
        self.assertEqual(unit_data.get("key"), "a-key")
        self.assertEqual(unit_data.get("auth"), "cephx")
        # But it does NOT own the app-level list - that is the Juju leader's job.
        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertNotIn("ceph-mon-public-addresses", app_data)

    def test_non_mon_unit_does_not_advertise_an_address(self):
        """A unit with no mon must not advertise a ceph-public-address.

        On a cluster larger than the mon count some units are OSD/mgr-only; their
        own IP is not in the mon list, so _lookup_system_interfaces returns "" and
        nothing is written. The Juju leader still publishes the full app list even
        when it is itself a non-mon unit.
        """
        self.harness.set_leader(True)
        # This unit's IP is not one of the cluster mon addresses.
        self.harness.charm._lookup_system_interfaces.return_value = ""

        rel_id = self._add_ceph_client_relation()

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertNotIn("ceph-public-address", unit_data)
        # The full mon list is still published by the (non-mon) Juju leader.
        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertEqual(json.loads(app_data["ceph-mon-public-addresses"]), self.MON_ADDRS)

    def test_update_status_republishes(self):
        """_on_update_status refreshes the mon data (self-healing path)."""
        self.harness.set_leader(True)
        rel_id = self._add_ceph_client_relation()
        # Drop what relation-changed published, then prove update-status restores it.
        self.harness.update_relation_data(
            rel_id, self.harness.charm.app.name, {"ceph-mon-public-addresses": ""}
        )

        # Fire the real event so the new observer registration is exercised; the
        # charm-level update-status handler is covered elsewhere, so silence it.
        with patch.object(self.harness.charm, "_on_update_status"):
            self.harness.charm.on.update_status.emit()

        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertEqual(json.loads(app_data["ceph-mon-public-addresses"]), self.MON_ADDRS)

    def test_peers_change_republishes(self):
        """A peers relation-changed event refreshes the published mon data."""
        self.harness.set_leader(True)
        rel_id = self._add_ceph_client_relation()
        self.harness.update_relation_data(
            rel_id, self.harness.charm.app.name, {"ceph-mon-public-addresses": ""}
        )

        # Fire a real peers relation-changed (a peer unit's data changes);
        # silence the cluster peer handler so only the ceph provider observer runs.
        peer_rel_id = self.harness.add_relation("peers", self.harness.charm.app.name)
        with patch.object(self.harness.charm.peers.interface, "on_changed"):
            self.harness.add_relation_unit(peer_rel_id, "microceph/1")
            self.harness.update_relation_data(
                peer_rel_id, "microceph/1", {"public-address": "10.0.0.3"}
            )

        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertEqual(json.loads(app_data["ceph-mon-public-addresses"]), self.MON_ADDRS)

    def test_stale_address_cleared_when_unit_leaves_mon_list(self):
        """A unit must stop advertising its address once it leaves the mon list.

        e.g. its mon is removed from the monmap, or Fix C's cross-check drops it.
        """
        self.harness.set_leader(True)
        rel_id = self._add_ceph_client_relation()
        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertEqual(unit_data.get("ceph-public-address"), self.SELF_ADDR)

        # This unit's IP is no longer a mon address; a refresh must clear it.
        self.harness.charm._lookup_system_interfaces.return_value = ""
        self.harness.update_relation_data(rel_id, "cinder-volume/0", {"broker_req": "{}"})

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertNotIn("ceph-public-address", unit_data)

    def test_publish_skipped_while_app_departing(self):
        """No mon data is published while the whole application is being removed."""
        self.harness.set_leader(True)
        self.harness.set_planned_units(0)  # planned_units()==0 -> is_departing True

        rel_id = self._add_ceph_client_relation()

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertNotIn("ceph-public-address", unit_data)
        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertNotIn("ceph-mon-public-addresses", app_data)

    def test_publish_swallows_fetch_errors(self):
        """A failure to fetch mon addresses must not crash the hook."""
        self.harness.set_leader(True)
        self.get_mon_addresses.side_effect = ConnectionError("socket gone")

        rel_id = self._add_ceph_client_relation()

        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertNotIn("ceph-public-address", unit_data)
        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertNotIn("ceph-mon-public-addresses", app_data)

    def test_publishes_to_radosgw_relation(self):
        """The shared publish logic also serves the radosgw provider relation."""
        self.harness.set_leader(True)
        rel_id = self.harness.add_relation(
            "radosgw", "ceph-radosgw", unit_data={"key_name": "rgw"}
        )

        app_data = self.harness.get_relation_data(rel_id, self.harness.charm.app.name)
        self.assertEqual(json.loads(app_data["ceph-mon-public-addresses"]), self.MON_ADDRS)
        unit_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.name)
        self.assertEqual(unit_data.get("ceph-public-address"), self.SELF_ADDR)


if __name__ == "__main__":
    unittest.main()
