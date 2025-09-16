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
from unittest.mock import patch

import ops_sunbeam.test_utils as test_utils
from unit import testbase

import ceph_rgw
import charm


class TestCephRgwClientProviderHandler(testbase.TestBaseCharm):

    def setUp(self):
        """Setup MicroCeph Charm tests."""
        super().setUp(ceph_rgw, [])
        with open("config.yaml", "r") as f:
            config_data = f.read()
        with open("metadata.yaml", "r") as f:
            metadata = f.read()

        patcher = patch.object(charm, "ceph_cos_agent")
        patcher.start()
        self.addCleanup(patcher.stop)

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
            ("run_cmd", "utils.run_cmd"),
            ("get_osd_count", "ceph.get_osd_count"),
        ]

        for attr_name, thing in patch_list:
            patcher = patch(thing)
            mock_obj = patcher.start()
            setattr(self, attr_name, mock_obj)
            self.addCleanup(patcher.stop)

        self._service_status = {}
        run_cmd_ret_value = self.run_cmd.return_value

        def _run_cmd(cmd):
            if cmd == ["sudo", "microceph.ceph", "service", "status"]:
                return json.dumps(self._service_status)
            return run_cmd_ret_value

        self.run_cmd.side_effect = _run_cmd

    def add_ceph_rgw_relation(self, app_name="consumer") -> int:
        """Add ceph-rgw-ready relation."""
        return self.harness.add_relation(
            ceph_rgw.CEPH_RGW_READY_RELATION,
            app_name,
            unit_data={"foo": "lish"},
        )

    def test_ceph_rgw_connected_ready(self):
        self.harness.update_config({"enable-rgw": "*"})
        self.ready_for_service.return_value = True
        self.get_osd_count.return_value = 3
        self._service_status = {
            "rgw": {
                "9999": {},
            },
        }

        self.harness.set_leader()
        self.add_ceph_rgw_relation()

        ceph_rgw_rel = self.harness.model.get_relation(ceph_rgw.CEPH_RGW_READY_RELATION)
        rel_data = ceph_rgw_rel.data[self.harness.model.app]
        self.assertEqual({"ready": "true"}, rel_data)

        self.ready_for_service.assert_called_once()
        self.get_osd_count.assert_called_once()
        self.run_cmd.assert_called_with(["sudo", "microceph.ceph", "service", "status"])

    def test_set_readiness_on_related_units(self):
        self.harness.update_config({"enable-rgw": ""})
        self.ready_for_service.return_value = False
        self.get_osd_count.return_value = 0

        self.harness.set_leader()
        self.add_ceph_rgw_relation()

        # enable-rgw config is set to "", disabling rgw.
        ceph_rgw_rel = self.harness.model.get_relation(ceph_rgw.CEPH_RGW_READY_RELATION)
        rel_data = ceph_rgw_rel.data[self.harness.model.app]
        self.assertEqual({"ready": "false"}, rel_data)

        self.ready_for_service.assert_not_called()
        self.get_osd_count.assert_not_called()
        self.run_cmd.assert_not_called()

        # rgw enabled, but charm is not yet ready for service.
        self.harness.update_config({"enable-rgw": "*"})

        self.ready_for_service.assert_called_once()
        self.get_osd_count.assert_not_called()

        # ready for service, but no OSDs yet.
        self.ready_for_service.return_value = True

        self.harness.charm.ceph_rgw.set_readiness_on_related_units()

        self.assertEqual({"ready": "false"}, rel_data)
        self.get_osd_count.assert_called_once()

        # has OSDs, but RF is higher.
        self.get_osd_count.return_value = 1

        self.harness.charm.ceph_rgw.set_readiness_on_related_units()

        self.assertEqual({"ready": "false"}, rel_data)

        # OSD count at least matches RF, but service is not available yet.
        self.harness.update_config({"default-pool-size": 1})

        self.harness.charm.ceph_rgw.set_readiness_on_related_units()

        self.assertEqual({"ready": "false"}, rel_data)
        self.run_cmd.assert_any_call(["sudo", "microceph.ceph", "service", "status"])

        # service is available.
        self._service_status = {
            "rgw": {
                "9999": {},
            },
        }

        self.harness.charm.ceph_rgw.set_readiness_on_related_units()

        self.assertEqual({"ready": "true"}, rel_data)


if __name__ == "__main__":
    unittest.main()
