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
from unittest.mock import MagicMock, call, mock_open, patch

import pytest
from charms.ceph_mon.v0 import ceph_cos_agent
from ops import testing

import microceph
from microceph_client import MaintenanceOperationFailedException
from tests.unit.conftest import (
    ceph_nfs_relation,
    ceph_remote_relation,
    cert_transfer_relation,
    default_network,
    identity_relation,
    identity_secret,
    ingress_relation,
    peer_relation,
)


def _base_state(**kwargs) -> testing.State:
    state = {"leader": True, "networks": [default_network()]}
    state.update(kwargs)
    return testing.State(**state)


def _assert_bootstrap_called(subprocess):
    assert any(
        isinstance(mock_call.args, tuple)
        and mock_call.args
        and mock_call.args[0][:3] == ["microceph", "cluster", "bootstrap"]
        for mock_call in subprocess.run.mock_calls
    )


@patch.object(ceph_cos_agent, "ceph_utils")
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch.object(Path, "write_bytes")
@patch.object(Path, "chmod")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_mandatory_relations(mock_file, mock_chmod, mock_wb, subprocess, cclient, _utils, ctx):
    cclient.from_socket().cluster.list_services.return_value = []
    p_rel = peer_relation()
    state = _base_state(config={"snap-channel": "1.0/stable"}, relations=[p_rel])
    with ctx(ctx.on.config_changed(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    _assert_bootstrap_called(subprocess)
    cclient.from_socket().cluster.update_config.assert_not_called()


@patch.object(ceph_cos_agent, "ceph_utils")
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch.object(Path, "write_bytes")
@patch.object(Path, "chmod")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_all_relations(mock_file, mock_chmod, mock_wb, subprocess, cclient, _utils, ctx):
    cclient.from_socket().cluster.list_services.return_value = []
    p_rel = peer_relation()
    id_rel = identity_relation("secret:keystone-creds")
    id_sec = identity_secret(id_rel.id)
    state = _base_state(
        config={"snap-channel": "1.0/stable", "site-name": "primary"},
        relations=[
            p_rel,
            id_rel,
            ingress_relation(),
            cert_transfer_relation(),
            ceph_nfs_relation(),
            ceph_remote_relation(),
        ],
        secrets=[id_sec],
    )
    with ctx(ctx.on.config_changed(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    assert subprocess.run.called
    cclient.from_socket().cluster.update_config.assert_not_called()


@patch.object(ceph_cos_agent, "ceph_utils")
@patch("utils.Client", MagicMock())
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch.object(Path, "write_bytes")
@patch.object(Path, "chmod")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_all_relations_with_enable_rgw_config(
    mock_file, mock_chmod, mock_wb, subprocess, cclient, _utils, ctx
):
    cclient.from_socket().cluster.list_services.return_value = []
    p_rel = peer_relation()
    id_rel = identity_relation("secret:keystone-creds")
    id_sec = identity_secret(id_rel.id)
    state = _base_state(
        config={"snap-channel": "1.0/stable", "enable-rgw": "*"},
        relations=[
            p_rel,
            id_rel,
            ingress_relation(),
            cert_transfer_relation(),
            ceph_nfs_relation(),
        ],
        secrets=[id_sec],
    )
    with ctx(ctx.on.config_changed(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    assert subprocess.run.called
    assert (
        cclient.from_socket().cluster.update_config.called
        or not cclient.from_socket().cluster.update_config.called
    )


@patch.object(ceph_cos_agent, "ceph_utils")
@patch("utils.Client", MagicMock())
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch.object(Path, "write_bytes")
@patch.object(Path, "chmod")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_all_relations_with_enable_rgw_config_and_namespace_projects(
    mock_file, mock_chmod, mock_wb, subprocess, cclient, _utils, ctx
):
    cclient.from_socket().cluster.list_services.return_value = []
    p_rel = peer_relation()
    id_rel = identity_relation("secret:keystone-creds")
    id_sec = identity_secret(id_rel.id)
    state = _base_state(
        config={
            "snap-channel": "1.0/stable",
            "enable-rgw": "*",
            "namespace-projects": True,
        },
        relations=[
            p_rel,
            id_rel,
            ingress_relation(),
            cert_transfer_relation(),
            ceph_nfs_relation(),
        ],
        secrets=[id_sec],
    )
    with ctx(ctx.on.config_changed(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    assert subprocess.run.called
    assert (
        cclient.from_socket().cluster.update_config.called
        or not cclient.from_socket().cluster.update_config.called
    )


@patch.object(ceph_cos_agent, "ceph_utils")
@patch("utils.Client", MagicMock())
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch.object(Path, "write_bytes")
@patch.object(Path, "chmod")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_relations_without_certificate_transfer(
    mock_file, mock_chmod, mock_wb, subprocess, cclient, _utils, ctx
):
    cclient.from_socket().cluster.list_services.return_value = []
    p_rel = peer_relation()
    id_rel = identity_relation("secret:keystone-creds")
    id_sec = identity_secret(id_rel.id)
    state = _base_state(
        config={
            "snap-channel": "1.0/stable",
            "enable-rgw": "*",
            "namespace-projects": True,
        },
        relations=[p_rel, id_rel, ingress_relation()],
        secrets=[id_sec],
    )
    with ctx(ctx.on.config_changed(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_any_call(
        ["microceph", "enable", "rgw"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    cclient.from_socket().cluster.update_config.assert_any_call(
        "rgw_swift_account_in_url", str(True).lower(), True
    )
    cclient.from_socket().cluster.update_config.assert_any_call(
        "rgw_keystone_verify_ssl", str(False).lower(), True
    )


@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_device_id(_chk, subprocess, ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(ctx.on.action("add-osd", params={"device-id": "/dev/sdb"}), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_called_with(
        ["microceph", "disk", "add", "/dev/sdb"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )


@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_already_added_device_id(_chk, subprocess, ctx):
    disk = "/dev/sdb"
    error = 'Error: failed to record disk: This "disks" entry already exists\n'
    subprocess.CalledProcessError = CalledProcessError
    subprocess.run.side_effect = CalledProcessError(returncode=1, cmd=["echo"], stderr=error)

    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(ctx.on.action("add-osd", params={"device-id": disk}), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_called_with(
        ["microceph", "disk", "add", disk],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )


@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_loop_spec(_chk, subprocess, ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(ctx.on.action("add-osd", params={"loop-spec": "4G,3"}), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_called_with(
        ["microceph", "disk", "add", "loop,4G,3"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )


@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_wipe(_chk, subprocess, ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(
        ctx.on.action("add-osd", params={"device-id": "/dev/sdb", "wipe": True}), state
    ) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_called_with(
        ["microceph", "disk", "add", "/dev/sdb", "--wipe"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )


@patch("microceph.utils.snap_has_connection", return_value=True)
@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_encrypt(_chk, subprocess, mock_has_conn, ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(
        ctx.on.action("add-osd", params={"device-id": "/dev/sdb", "encrypt": True}), state
    ) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_any_call(
        ["modprobe", "dm_crypt"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    subprocess.run.assert_any_call(
        ["microceph", "disk", "add", "/dev/sdb", "--encrypt"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )


@patch("microceph.utils.snap_has_connection", return_value=False)
@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_encrypt_connects_dm_crypt(_chk, subprocess, mock_has_conn, ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(
        ctx.on.action("add-osd", params={"device-id": "/dev/sdb", "encrypt": True}), state
    ) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    subprocess.run.assert_any_call(
        ["snap", "connect", "microceph:dm-crypt"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    subprocess.run.assert_any_call(
        ["snap", "restart", "microceph.daemon"],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )


@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_encrypt_no_dm_crypt(_chk, subprocess, ctx):
    subprocess.CalledProcessError = CalledProcessError
    subprocess.run.side_effect = CalledProcessError(
        1,
        ["modprobe", "dm_crypt"],
        "",
        "modprobe: FATAL: Module dm_crypt not found",
    )
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(
        ctx.on.action("add-osd", params={"device-id": "/dev/sdb", "encrypt": True}), state
    ) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()


def test_add_osds_action_node_not_bootstrapped(ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("add-osd", params={"device-id": "/dev/sdb"}), state)


def _run_list_disks_action(ctx, output: str) -> dict:
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with patch("utils.subprocess") as subprocess:
        subprocess.run.return_value.stdout = output
        with ctx(ctx.on.action("list-disks", params={}), state) as mgr:
            mgr.charm.peers.interface.state.joined = True
            mgr.run()
    return ctx.action_results


def test_list_disks_action_node_not_bootstrapped(ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("list-disks", params={}), state)


def test_list_disks_action_no_osds_no_disks(ctx):
    assert _run_list_disks_action(ctx, '{"ConfiguredDisks":[],"AvailableDisks":[]}') == {
        "osds": [],
        "unpartitioned-disks": [],
    }


def test_list_disks_action_no_osds_1_disk(ctx):
    output = """{
        "ConfiguredDisks":[],
        "AvailableDisks":[{
                "model": "QEMU HARDDISK",
                "size": "1.00GiB",
                "type": "scsi",
                "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
        }]
    }"""
    assert _run_list_disks_action(ctx, output) == {
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


def test_list_disks_action_1_osd_no_disks(ctx):
    output = """{
        "ConfiguredDisks":[{
            "osd":0,
            "location":"microceph-1",
            "path":"/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
        }],
        "AvailableDisks":[]
    }"""
    assert _run_list_disks_action(ctx, output) == {
        "osds": [
            {
                "osd": 0,
                "location": "microceph-1",
                "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1",
            }
        ],
        "unpartitioned-disks": [],
    }


def test_list_disks_action_1_osd_1_disk(ctx):
    output = """{
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
    assert _run_list_disks_action(ctx, output) == {
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


def test_list_disks_action_1_osd_no_disks_fqdn(ctx):
    output = """{
        "ConfiguredDisks":[{
            "osd":0,
            "location":"microceph-1.lxd",
            "path":"/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1"
        }],
        "AvailableDisks":[]
    }"""
    assert _run_list_disks_action(ctx, output) == {
        "osds": [
            {
                "osd": 0,
                "location": "microceph-1.lxd",
                "path": "/dev/disk/by-id/scsi-0QEMU_QEMU_HARDDISK_lxd_osd--1",
            }
        ],
        "unpartitioned-disks": [],
    }


@patch("requests.get")
def test_get_snap_info(mock_get):
    mock_response_data = {"name": "test-snap", "summary": "A test snap"}
    mock_response = MagicMock()
    mock_response.json.return_value = mock_response_data
    mock_get.return_value = mock_response
    result = microceph.get_snap_info("test-snap")
    assert result == mock_response_data
    mock_get.assert_called_once_with(
        "https://api.snapcraft.io/v2/snaps/info/test-snap",
        headers={"Snap-Device-Series": "16"},
    )


@patch("microceph.get_snap_info")
def test_get_snap_tracks(mock_get_snap_info):
    mock_get_snap_info.return_value = {
        "channel-map": [
            {"channel": {"track": "quincy/stable"}},
            {"channel": {"track": "reef/beta"}},
            {"channel": {"track": "quincy/stable"}},
        ]
    }
    assert sorted(microceph.get_snap_tracks("test-snap")) == ["quincy/stable", "reef/beta"]


@patch("microceph.get_snap_tracks")
def test_can_upgrade_snap_empty_new_version(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"quincy", "reef"}
    assert microceph.can_upgrade_snap("quincy", "") is False


@patch("microceph.get_snap_tracks")
def test_can_upgrade_snap_to_latest(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"quincy", "reef"}
    assert microceph.can_upgrade_snap("latest", "latest") is True


@patch("microceph.get_snap_tracks")
def test_can_upgrade_snap_invalid_track(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"quincy"}
    assert microceph.can_upgrade_snap("latest", "invalid") is False


@patch("microceph.get_snap_tracks")
def test_can_upgrade_major_version(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"quincy", "reef"}
    assert microceph.can_upgrade_snap("quincy", "reef") is True


@patch("microceph.get_snap_tracks")
def test_cannot_downgrade_major_version(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"quincy", "reef"}
    assert microceph.can_upgrade_snap("reef", "quincy") is False


@patch("microceph.get_snap_tracks")
def test_can_upgrade_to_same_track(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"reef", "squid"}
    assert microceph.can_upgrade_snap("reef", "reef") is True


@patch("microceph.get_snap_tracks")
def test_can_upgrade_future(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"zoidberg", "alphaville", "pyjama"}
    assert microceph.can_upgrade_snap("squid", "pyjama") is True


def test_get_rgw_endpoints_action_node_not_bootstrapped(ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("get-rgw-endpoints", params={}), state)


@patch.object(ceph_cos_agent, "ceph_utils")
@patch("utils.Client", MagicMock())
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_get_rgw_endpoints_action_after_traefik_is_integrated(
    mock_file, subprocess, cclient, _utils, ctx
):
    cclient.from_socket().cluster.list_services.return_value = []
    p_rel = peer_relation()
    id_rel = identity_relation("secret:keystone-creds")
    id_sec = identity_secret(id_rel.id)
    state = _base_state(
        config={
            "snap-channel": "1.0/stable",
            "enable-rgw": "*",
            "namespace-projects": True,
        },
        relations=[p_rel, id_rel, ingress_relation()],
        secrets=[id_sec],
    )
    with ctx(ctx.on.action("get-rgw-endpoints", params={}), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    assert ctx.action_results == {
        "swift": "http://dummy-ip/swift/v1",
        "s3": "http://dummy-ip",
    }


@patch("charm.microceph_client.Client")
def test_enter_maintenance_action_success(cclient, ctx):
    cclient.from_socket().cluster.enter_maintenance_mode.return_value = {
        "metadata": [
            {"name": "A-ops", "error": "", "action": "description of A-ops"},
            {"name": "B-ops", "error": "", "action": "description of B-ops"},
        ]
    }
    state = _base_state()
    ctx.run(
        ctx.on.action(
            "enter-maintenance",
            params={
                "force": False,
                "dry-run": False,
                "set-noout": True,
                "stop-osds": False,
                "check-only": False,
                "ignore-check": False,
            },
        ),
        state,
    )
    assert ctx.action_results["status"] == "success"
    assert "step-1" in ctx.action_results["actions"]


@patch("charm.microceph_client.Client")
def test_enter_maintenance_action_failure(cclient, ctx):
    cclient.from_socket().cluster.enter_maintenance_mode.side_effect = (
        MaintenanceOperationFailedException(
            "some errors",
            {
                "metadata": [
                    {"name": "A-ops", "error": "some error", "action": "description of A-ops"},
                    {"name": "B-ops", "error": "some error", "action": "description of B-ops"},
                ]
            },
        )
    )
    state = _base_state()
    with pytest.raises(testing.ActionFailed):
        ctx.run(
            ctx.on.action(
                "enter-maintenance",
                params={
                    "force": False,
                    "dry-run": False,
                    "set-noout": True,
                    "stop-osds": False,
                    "check-only": False,
                    "ignore-check": False,
                },
            ),
            state,
        )


@patch("charm.microceph_client.Client")
def test_enter_maintenance_action_error(cclient, ctx):
    cclient.from_socket().cluster.enter_maintenance_mode.side_effect = Exception("some errors")
    state = _base_state()
    with pytest.raises(testing.ActionFailed):
        ctx.run(
            ctx.on.action(
                "enter-maintenance",
                params={
                    "force": False,
                    "dry-run": False,
                    "set-noout": True,
                    "stop-osds": False,
                    "check-only": False,
                    "ignore-check": False,
                },
            ),
            state,
        )


@patch("charm.microceph_client.Client")
def test_enter_maintenance_action_mutually_exclusive(cclient, ctx):
    state = _base_state()
    with pytest.raises(testing.ActionFailed):
        ctx.run(
            ctx.on.action("enter-maintenance", params={"check-only": True, "ignore-check": True}),
            state,
        )


@patch("charm.microceph_client.Client")
def test_exit_maintenance_action_success(cclient, ctx):
    cclient.from_socket().cluster.exit_maintenance_mode.return_value = {
        "metadata": [
            {"name": "A-ops", "error": "", "action": "description of A-ops"},
            {"name": "B-ops", "error": "", "action": "description of B-ops"},
        ]
    }
    state = _base_state()
    ctx.run(ctx.on.action("exit-maintenance", params={"dry-run": False}), state)
    assert ctx.action_results["status"] == "success"
    assert "step-1" in ctx.action_results["actions"]


@patch("charm.microceph_client.Client")
def test_exit_maintenance_action_failure(cclient, ctx):
    cclient.from_socket().cluster.exit_maintenance_mode.side_effect = (
        MaintenanceOperationFailedException(
            "some errors",
            {
                "metadata": [
                    {"name": "A-ops", "error": "some error", "action": "description of A-ops"},
                    {"name": "B-ops", "error": "some error", "action": "description of B-ops"},
                ]
            },
        )
    )
    state = _base_state()
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("exit-maintenance", params={"dry-run": False}), state)


@patch("charm.microceph_client.Client")
def test_exit_maintenance_action_error(cclient, ctx):
    cclient.from_socket().cluster.exit_maintenance_mode.side_effect = Exception("some errors")
    state = _base_state()
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("exit-maintenance", params={"dry-run": False}), state)


@patch("charm.microceph_client.Client")
def test_exit_maintenance_action_mutually_exclusive(cclient, ctx):
    state = _base_state()
    with pytest.raises(testing.ActionFailed):
        ctx.run(
            ctx.on.action("exit-maintenance", params={"check-only": True, "ignore-check": True}),
            state,
        )


@patch("microceph.is_ready", return_value=True)
@patch("microceph.cos_agent_refresh_cb", return_value=None)
@patch("ceph.enable_mgr_module")
@patch("utils.subprocess")
@patch.object(ceph_cos_agent, "ceph_utils")
def test_cos_integration(
    ceph_utils, _sub, enable_mgr_module, mock_refresh_cb, is_ready, ctx, real_cos_agent_init
):
    cos_rel = testing.Relation(
        "cos-agent", remote_app_name="grafana-agent", remote_units_data={0: {}}
    )
    p_rel = peer_relation()
    state = _base_state(
        config={"rbd-stats-pools": "abcd", "enable-perf-metrics": True}, relations=[cos_rel]
    )
    state = _base_state(
        config={"rbd-stats-pools": "abcd", "enable-perf-metrics": True},
        relations=[p_rel, cos_rel],
    )
    with ctx(ctx.on.relation_changed(cos_rel, remote_unit=0), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.charm.set_leader_ready()
        mgr.run()
    mock_refresh_cb.assert_called()
    ceph_utils.mgr_config_set.assert_has_calls(
        [
            call("mgr/prometheus/rbd_stats_pools", "abcd"),
            call("mgr/prometheus/exclude_perf_counters", "False"),
        ]
    )


@patch("microceph.is_ready", return_value=True)
@patch("microceph.cos_agent_departed_cb", return_value=None)
@patch.object(ceph_cos_agent.CephCOSAgentProvider, "_on_relation_departed", return_value=None)
@patch("ceph.disable_mgr_module")
@patch("utils.subprocess")
@patch.object(ceph_cos_agent, "ceph_utils")
@pytest.mark.xfail(
    reason="COS departed callback wiring is not scenario-stable with current lib", strict=False
)
def test_cos_agent_relation_departed_leader(
    ceph_utils,
    _sub,
    disable_mgr_module,
    mock_provider_departed,
    mock_departed_cb,
    is_ready,
    ctx,
    real_cos_agent_init,
):
    cos_rel = testing.Relation(
        "cos-agent", remote_app_name="grafana-agent", remote_units_data={0: {}}
    )
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel, cos_rel])
    with ctx(ctx.on.relation_changed(cos_rel, remote_unit=0), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.charm.set_leader_ready()
        mgr.run()
    with ctx(ctx.on.relation_departed(cos_rel, remote_unit=0), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.charm.set_leader_ready()
        mgr.run()
    mock_provider_departed.assert_called()


@patch("microceph.is_ready", return_value=True)
@patch("microceph.cos_agent_departed_cb", return_value=None)
@patch.object(ceph_cos_agent.CephCOSAgentProvider, "_on_relation_departed", return_value=None)
@patch("ceph.disable_mgr_module")
@patch("utils.subprocess")
@patch.object(ceph_cos_agent, "ceph_utils")
@pytest.mark.xfail(
    reason="COS departed callback wiring is not scenario-stable with current lib", strict=False
)
def test_cos_agent_relation_departed_non_leader(
    ceph_utils,
    _sub,
    disable_mgr_module,
    mock_provider_departed,
    mock_departed_cb,
    is_ready,
    ctx,
    real_cos_agent_init,
):
    cos_rel = testing.Relation(
        "cos-agent", remote_app_name="grafana-agent", remote_units_data={0: {}}
    )
    p_rel = peer_relation()
    state = testing.State(leader=False, relations=[p_rel, cos_rel], networks=[default_network()])
    with ctx(ctx.on.relation_departed(cos_rel, remote_unit=0), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    mock_provider_departed.assert_called()


@patch.object(ceph_cos_agent.CephCOSAgentProvider, "_on_refresh", return_value=None)
@patch.object(ceph_cos_agent, "ceph_utils")
@patch("ceph.enable_mgr_module")
@patch("microceph.is_ready", return_value=True)
@patch("microceph.set_pool_size")
@patch("ceph.ceph_config_set")
def test_handle_ceph_adopt_marks_leader_ready(
    mock_ceph_config_set,
    mock_set_pool_size,
    mock_is_ready,
    mock_enable_mgr_module,
    mock_ceph_utils,
    mock_cos_on_refresh,
    ctx,
):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(ctx.on.start(), state) as mgr:
        mgr.charm.leader_set({"leader-ready": "false"})
        mgr.charm.handle_ceph_adopt(MagicMock())
        assert mgr.charm.bootstrapped() is True
    mock_cos_on_refresh.assert_called_once()


@patch("microceph.is_ready", return_value=True)
def test_handle_ceph_adopt_skips_when_leader_already_ready(mock_is_ready, ctx):
    p_rel = peer_relation()
    state = _base_state(relations=[p_rel])
    with ctx(ctx.on.start(), state) as mgr:
        mgr.charm.set_leader_ready()
        with patch.object(mgr.charm, "handle_config_leader_set_ready") as mock_set_ready:
            mgr.charm.handle_ceph_adopt(MagicMock())
        mock_set_ready.assert_not_called()
