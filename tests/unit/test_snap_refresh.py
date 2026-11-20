# Copyright 2026 Canonical Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

"""Tests for same-channel MicroCeph snap refreshes."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.test_utils as test_utils
import pytest
from ops.testing import ActionFailed
from unit import testbase

import charm
from ceph import CephHealth


class TestSnapRefresh(testbase.TestBaseCharm):
    def setUp(self):
        super().setUp(charm, [])
        self.harness = test_utils.get_harness(
            testbase._MicroCephCharm,
            container_calls=self.container_calls,
            charm_config=Path("config.yaml").read_text(),
            charm_metadata=Path("metadata.yaml").read_text(),
            charm_actions=Path("actions.yaml").read_text(),
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_leader()
        self.unit_charm = self.harness.charm
        self.harness.disable_hooks()
        self.relation = self.harness.add_relation("peers", self.unit_charm.app.name)
        for unit in ("microceph/1", "microceph/2"):
            self.harness.add_relation_unit(self.relation, unit)
            self.harness.update_relation_data(self.relation, unit, {"joined": "true"})
        self.harness.update_config({"snap-channel": "squid/stable"})
        self.unit_charm.channel = "squid/stable"
        self.harness.enable_hooks()
        self.peers = self.unit_charm.cluster_upgrades.peer_int
        self.installed = self.enterContext(patch("cluster.snap.SnapCache"))
        self.installed.return_value.__getitem__.return_value.revision = "1895"
        self.store = self.enterContext(patch("cluster.microceph.get_snap_info"))
        self.store.return_value = {"channels": {"squid/stable": {"revision": "2136"}}}
        self.health = self.enterContext(patch("cluster.CephStatus"))
        self.health.return_value.ceph_health.return_value = (CephHealth.Ok, "")
        self.enterContext(patch.object(self.unit_charm, "ready_for_service", return_value=True))

    def test_refresh_queues_one_revision_on_unchanged_channel(self):
        result = self.harness.run_action("cluster-snap-refresh")
        self.assertEqual(result.results["revision"], "2136")
        plan = self.peers.get_upgrade_info()
        self.assertEqual(plan["nodes"], ["microceph/0", "microceph/1", "microceph/2"])
        self.assertEqual(plan["channel"], "squid/stable")
        self.assertEqual(plan["revision"], "2136")

    def test_refresh_is_a_noop_if_installed_revision_matches(self):
        self.installed.return_value.__getitem__.return_value.revision = "2136"
        result = self.harness.run_action("cluster-snap-refresh")
        self.assertEqual(result.results["status"], "up-to-date")
        self.assertFalse(self.peers.get_upgrade_info())

    def test_unhealthy_cluster_cannot_start_refresh(self):
        self.health.return_value.ceph_health.return_value = (CephHealth.Warn, "degraded")
        with pytest.raises(ActionFailed, match="ceph health not ok"):
            self.harness.run_action("cluster-snap-refresh")
        self.assertFalse(self.peers.get_upgrade_info())

    def test_failed_peer_does_not_advance_and_retry_uses_pinned_revision(self):
        self.harness.run_action("cluster-snap-refresh")
        with patch.object(self.unit_charm.cluster_upgrades, "perform_upgrade") as perform:
            self.unit_charm.on.update_status.emit()
            perform.assert_called_once_with("squid/stable", "2136")
        plan = self.peers.get_upgrade_info()
        self.assertEqual(plan["nodes"], ["microceph/1", "microceph/2"])
        self.assertEqual(plan["revision"], "2136")

        # Replay the plan with the harness unit as the first follower.
        self.peers.set_upgrade_info(
            plan["nonce"], "squid/stable", ["microceph/0", "microceph/2"], "2136"
        )
        plan = self.peers.get_upgrade_info()
        with self.harness.hooks_disabled():
            self.harness.set_leader(False)
        unit_data = self.peers.peers_rel.data[self.unit_charm.unit]
        with patch.object(self.unit_charm.cluster_upgrades, "perform_upgrade") as perform:
            perform.side_effect = [subprocess.CalledProcessError(1, "snap"), None]
            with pytest.raises(subprocess.CalledProcessError):
                self.unit_charm._on_update_status(None)
            self.assertNotIn("upgrade-done", unit_data)
            self.assertEqual(self.peers.get_upgrade_info(), plan)

            self.unit_charm._on_update_status(None)
            self.assertEqual(unit_data["upgrade-done"], plan["nonce"])
            perform.assert_called_with("squid/stable", "2136")
            self.unit_charm._on_update_status(None)
            self.assertEqual(perform.call_count, 2)

    def test_snap_refresh_targets_pinned_revision(self):
        with (
            patch("cluster.subprocess.run", side_effect=subprocess.CalledProcessError(1, "pgrep")),
            patch("cluster.tenacity.nap.time.sleep"),
            patch("cluster.utils.run_cmd") as run_cmd,
        ):
            self.unit_charm.cluster_upgrades.perform_upgrade("squid/stable", "2136")
        run_cmd.assert_called_once_with(
            ["snap", "refresh", "microceph", "--revision=2136"], timeout=900
        )

    def test_health_is_checked_before_a_queued_unit_refreshes(self):
        self.health.return_value.ceph_health.return_value = (CephHealth.Warn, "degraded")
        with pytest.raises(sunbeam_guard.BlockedExceptionError, match="ceph health not ok"):
            self.unit_charm.cluster_upgrades.perform_upgrade("squid/stable", "2136")
