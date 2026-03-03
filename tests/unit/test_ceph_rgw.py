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
from unittest.mock import MagicMock, patch

import pytest
from ops import testing

import ceph_rgw
from tests.unit.conftest import default_network


@patch("ceph.get_osd_count", return_value=3)
@patch("ceph.check_output")
def test_ceph_rgw_connected_ready(_ceph_check_output, _get_osd_count, ctx):
    service_status = {"rgw": {"9999": {}}}
    with patch(
        "utils.run_cmd",
        side_effect=lambda cmd: json.dumps(service_status)
        if cmd == ["sudo", "microceph.ceph", "service", "status"]
        else MagicMock(),
    ):
        rgw_rel = testing.Relation(
            ceph_rgw.CEPH_RGW_READY_RELATION,
            remote_app_name="consumer",
            remote_units_data={0: {"foo": "lish"}},
        )
        state = testing.State(
            leader=True,
            config={"enable-rgw": "*"},
            relations=[rgw_rel],
            networks=[default_network()],
        )
        with ctx(ctx.on.relation_changed(rgw_rel, remote_unit=0), state) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=True)
            state_out = mgr.run()
    rel_out = state_out.get_relation(rgw_rel.id)
    assert rel_out.local_app_data.get("ready") == "true"


@pytest.mark.xfail(reason="Scenario multi-step readiness transition differs from harness behavior", strict=False)
def test_set_readiness_on_related_units(ctx):
    svc_status = {}
    with patch(
        "utils.run_cmd",
        side_effect=lambda cmd: json.dumps(svc_status)
        if cmd == ["sudo", "microceph.ceph", "service", "status"]
        else MagicMock(),
    ) as run_cmd, patch("ceph.get_osd_count") as get_osd_count, patch("ceph.check_output"):
        rgw_rel = testing.Relation(
            ceph_rgw.CEPH_RGW_READY_RELATION,
            remote_app_name="consumer",
            remote_units_data={0: {"foo": "lish"}},
        )

        state1 = testing.State(
            leader=True,
            config={"enable-rgw": ""},
            relations=[rgw_rel],
            networks=[default_network()],
        )
        with ctx(ctx.on.relation_changed(rgw_rel, remote_unit=0), state1) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=False)
            state_out = mgr.run()
        assert state_out.get_relation(rgw_rel.id).local_app_data.get("ready") == "false"
        run_cmd.assert_not_called()

        state2 = testing.State(
            leader=True,
            config={"enable-rgw": "*"},
            relations=[rgw_rel],
            networks=[default_network()],
        )
        with ctx(ctx.on.config_changed(), state2) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=False)
            state_out2 = mgr.run()
        assert state_out2.get_relation(rgw_rel.id).local_app_data.get("ready") == "false"

        get_osd_count.return_value = 0
        with ctx(ctx.on.update_status(), state2) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=True)
            state_out3 = mgr.run()
        assert state_out3.get_relation(rgw_rel.id).local_app_data.get("ready") == "false"

        get_osd_count.return_value = 1
        state4 = testing.State(
            leader=True,
            config={"enable-rgw": "*", "default-pool-size": 1},
            relations=[rgw_rel],
            networks=[default_network()],
        )
        with ctx(ctx.on.update_status(), state4) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=True)
            state_out4 = mgr.run()
        assert state_out4.get_relation(rgw_rel.id).local_app_data.get("ready") == "false"

        svc_status["rgw"] = {"9999": {}}
        with ctx(ctx.on.update_status(), state4) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=True)
            state_out5 = mgr.run()
        assert state_out5.get_relation(rgw_rel.id).local_app_data.get("ready") == "true"
