# ops-scenario Migration: Wave 2 & 3 Agent Prompts

> **Context:** Wave 1 is complete (commit 957ad98). `test-requirements.txt` uses `ops[testing]<3.4`,
> `testbase.py` is gutted to `DUMMY_CA_CERT` only, `conftest.py` exists with all fixtures.
> The 6 harness-based test files below currently **fail** because `testbase.TestBaseCharm` is gone.

---

## Wave 2: Parallel migration (run all 3 agents at once)

**Infrastructure reminder:** `conftest.py` provides `cos_agent_patched` (autouse), `ctx` fixture, and helpers:
`peer_relation()`, `default_network()`, `identity_relation(secret_id)`, `identity_secret(relation_id)`,
`ingress_relation()`, `cert_transfer_relation()`, `ceph_nfs_relation(app_name)`, `ceph_remote_relation(relation_name)`.
Import via `from tests.unit.conftest import ...`.

**Manager pattern** for direct charm attribute access before event dispatch:
```python
with ctx(ctx.on.config_changed(), state) as mgr:
    mgr.charm.peers.interface.state.joined = True  # set before dispatch
    state_out = mgr.run()
```

**Setting initial unit status:** `State(unit_status=BlockedStatus("msg"))` maps to
`harness.charm.status.set(BlockedStatus("msg"))`.

---

### Agent A — Migrate `tests/unit/test_charm.py` (~45 harness tests)

**File to rewrite:** `tests/unit/test_charm.py`

Remove the entire `class TestCharm(testbase.TestBaseCharm)` body and replace with pytest functions.

#### Group 1 – Pure Python helper tests (NO scenario needed)

These tests call standalone `microceph` module functions — no charm instantiation needed.
Migrate as plain pytest functions (no `testing.Context`):

```python
# Tests: test_get_snap_info, test_get_snap_tracks, test_can_upgrade_snap_empty_new_version,
# test_can_upgrade_snap_to_latest, test_can_upgrade_snap_invalid_track, test_can_upgrade_major_version,
# test_cannot_downgrade_major_version, test_can_upgrade_to_same_track, test_can_upgrade_future

@patch("requests.get")
def test_get_snap_info(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "test-snap"}
    mock_get.return_value = mock_response
    result = microceph.get_snap_info("test-snap")
    assert result == {"name": "test-snap"}
    mock_get.assert_called_once_with(
        "https://api.snapcraft.io/v2/snaps/info/test-snap",
        headers={"Snap-Device-Series": "16"},
    )

@patch("microceph.get_snap_tracks")
def test_can_upgrade_snap_to_latest(mock_get_snap_tracks):
    mock_get_snap_tracks.return_value = {"quincy", "reef"}
    assert microceph.can_upgrade_snap("latest", "latest") is True
# Repeat for all can_upgrade_snap_* and get_snap_tracks tests — same pattern, no charm needed
```

#### Group 2 – Relation/lifecycle tests

Pattern for `test_mandatory_relations`:

```python
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch
from ops import testing
import charm
import microceph
from charms.ceph_mon.v0 import ceph_cos_agent
from tests.unit.conftest import (
    peer_relation, default_network, identity_relation, identity_secret,
    ingress_relation, cert_transfer_relation, ceph_nfs_relation, ceph_remote_relation,
)

@patch.object(ceph_cos_agent, "ceph_utils")
@patch.object(microceph, "Client")
@patch("utils.subprocess")
@patch.object(Path, "write_bytes")
@patch.object(Path, "chmod")
@patch("builtins.open", new_callable=mock_open, read_data="mon host dummy-ip")
def test_mandatory_relations(mock_file, mock_chmod, mock_wb, subprocess, cclient, _utils):
    cclient.from_socket().cluster.list_services.return_value = []
    peer_rel = peer_relation()
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(
        leader=True,
        config={"snap-channel": "1.0/stable"},
        relations=[peer_rel],
        networks=[default_network()],
    )
    ctx.run(ctx.on.relation_changed(peer_rel), state)
    subprocess.run.assert_any_call(
        ["microceph", "cluster", "bootstrap",
         "--public-network", "10.0.0.0/24",
         "--cluster-network", "10.0.0.0/24",
         "--microceph-ip", "10.0.0.10"],
        capture_output=True, text=True, check=True, timeout=180,
    )
    cclient.from_socket().cluster.update_config.assert_not_called()
```

For `test_all_relations`, build state with all relations and add `identity_secret`:
```python
peer_rel = peer_relation()
id_rel = identity_relation("secret:keystone-creds")
id_sec = identity_secret(id_rel.id)
state = testing.State(
    leader=True, config={"snap-channel": "1.0/stable", "site-name": "primary"},
    relations=[peer_rel, id_rel, ingress_relation(), cert_transfer_relation(),
               ceph_nfs_relation(), ceph_remote_relation()],
    secrets=[id_sec], networks=[default_network()],
)
```
For RGW tests add `config={"enable-rgw": "*"}` and assert `subprocess.run` called with `["microceph", "enable", "rgw"]`.

#### Group 3 – OSD action tests

Action name is `"add-osd"` (from `actions.yaml`). Use Manager pattern to set `peers.interface.state.joined = True`:

```python
from subprocess import CalledProcessError

@patch("utils.subprocess")
@patch("ceph.check_output")
def test_add_osds_action_with_device_id(_chk, subprocess):
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = peer_relation()
    state = testing.State(leader=True, relations=[peer_rel], networks=[default_network()])
    with ctx(ctx.on.action("add-osd", params={"device-id": "/dev/sdb"}), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        state_out = mgr.run()
    subprocess.run.assert_called_with(
        ["microceph", "disk", "add", "/dev/sdb"],
        capture_output=True, text=True, check=True, timeout=180,
    )

def test_add_osds_action_node_not_bootstrapped():
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = peer_relation()
    state = testing.State(leader=True, relations=[peer_rel], networks=[default_network()])
    # peers.interface.state.joined is False by default → action fails
    with pytest.raises(testing.ActionFailed) as exc:
        ctx.run(ctx.on.action("add-osd", params={"device-id": "/dev/sdb"}), state)
    assert "not yet joined" in exc.value.message
```

- For `loop-spec`: `params={"loop-spec": "4G,3"}` → assert `["microceph", "disk", "add", "loop,4G,3"]`
- For `wipe=True`: `params={"device-id": "/dev/sdb", "wipe": True}` → assert `["microceph", "disk", "add", "/dev/sdb", "--wipe"]`
- For `encrypt=True`: also patch `microceph.utils.snap_has_connection`, assert modprobe call and disk add with `--encrypt`

#### Group 4 – List-disks action tests

Action name is `"list-disks"`. Handler reads subprocess stdout for JSON:
```python
@patch("utils.subprocess")
def test_list_disks_action_no_osds_no_disks(subprocess):
    subprocess.run.return_value.stdout = '{"ConfiguredDisks":[],"AvailableDisks":[]}'
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = peer_relation()
    state = testing.State(leader=True, relations=[peer_rel], networks=[default_network()])
    with ctx(ctx.on.action("list-disks", params={}), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.run()
    assert ctx.action_results == {"osds": [], "unpartitioned-disks": []}
```
Repeat for 1-OSD, 1-disk, FQDN location variants with different stdout JSON.

#### Group 5 – Maintenance action tests (enter-maintenance, exit-maintenance)

```python
from microceph_client import MaintenanceOperationFailedException

@patch("charm.microceph_client.Client")
def test_enter_maintenance_action_success(cclient):
    cclient.from_socket().cluster.enter_maintenance_mode.return_value = {
        "metadata": [
            {"name": "A-ops", "error": "", "action": "description of A-ops"},
            {"name": "B-ops", "error": "", "action": "description of B-ops"},
        ],
    }
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(leader=True, networks=[default_network()])
    ctx.run(ctx.on.action("enter-maintenance", params={
        "force": False, "dry-run": False, "set-noout": True,
        "stop-osds": False, "check-only": False, "ignore-check": False,
    }), state)
    assert ctx.action_results["status"] == "success"
    assert "step-1" in ctx.action_results["actions"]

@patch("charm.microceph_client.Client")
def test_enter_maintenance_action_failure(cclient):
    cclient.from_socket().cluster.enter_maintenance_mode.side_effect = \
        MaintenanceOperationFailedException("some errors", {"metadata": [...]})
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(leader=True, networks=[default_network()])
    with pytest.raises(testing.ActionFailed):
        ctx.run(ctx.on.action("enter-maintenance", params={
            "force": False, "dry-run": False, "set-noout": True,
            "stop-osds": False, "check-only": False, "ignore-check": False,
        }), state)
```
- For `mutually_exclusive`: `params={"check-only": True, "ignore-check": True}` → `ActionFailed`
- Repeat same pattern for `exit-maintenance` using `exit_maintenance_mode` mock

#### Group 6 – COS integration tests

```python
@patch("microceph.is_ready", return_value=True)
@patch("ceph.enable_mgr_module")
@patch("utils.subprocess")
@patch.object(ceph_cos_agent, "ceph_utils")
def test_cos_integration(ceph_utils, _sub, enable_mgr_module, is_ready):
    ctx = testing.Context(charm.MicroCephCharm)
    cos_rel = testing.Relation("cos-agent", remote_app_name="grafana-agent",
                               remote_units_data={0: {}})
    state = testing.State(leader=True, config={"rbd-stats-pools": "abcd", "enable-perf-metrics": True},
                          relations=[cos_rel], networks=[default_network()])
    ctx.run(ctx.on.relation_changed(cos_rel), state)
    enable_mgr_module.assert_called_once_with("prometheus")

@patch("microceph.is_ready", return_value=True)
@patch("ceph.disable_mgr_module")
@patch("utils.subprocess")
@patch.object(ceph_cos_agent, "ceph_utils")
def test_cos_agent_relation_departed_leader(ceph_utils, _sub, disable_mgr_module, is_ready):
    ctx = testing.Context(charm.MicroCephCharm)
    cos_rel = testing.Relation("cos-agent", remote_app_name="grafana-agent", remote_units_data={0: {}})
    state = testing.State(leader=True, relations=[cos_rel], networks=[default_network()])
    ctx.run(ctx.on.relation_departed(cos_rel), state)
    disable_mgr_module.assert_called_once_with("prometheus")

# Non-leader variant: state=testing.State(leader=False, ...) → disable_mgr_module.assert_not_called()
```

#### Group 7 – Adopt-ceph tests

```python
@patch("microceph.is_ready", return_value=True)
@patch("ceph.enable_mgr_module")
@patch("microceph.set_pool_size")
@patch("ceph.ceph_config_set")
def test_handle_ceph_adopt_marks_leader_ready(*mocks):
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = peer_relation()
    state = testing.State(leader=True, relations=[peer_rel], networks=[default_network()])
    with ctx(ctx.on.relation_changed(peer_rel), state) as mgr:
        mgr.charm.leader_set({"leader-ready": "false"})
        event = MagicMock()
        mgr.charm.handle_ceph_adopt(event)
        assert mgr.charm.bootstrapped() is True

@patch("microceph.is_ready", return_value=True)
def test_handle_ceph_adopt_skips_when_leader_already_ready(mock_is_ready):
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = peer_relation()
    state = testing.State(leader=True, relations=[peer_rel], networks=[default_network()])
    with ctx(ctx.on.relation_changed(peer_rel), state) as mgr:
        mgr.charm.set_leader_ready()
        with patch.object(mgr.charm, "handle_config_leader_set_ready") as mock_set_ready:
            mgr.charm.handle_ceph_adopt(MagicMock())
        mock_set_ready.assert_not_called()
```

**Verification:**
```bash
tox -e py3 -- tests/unit/test_charm.py -v
```

---

### Agent B — Migrate `tests/unit/test_ceph_nfs.py` (8 tests) + `tests/unit/test_ceph_rgw.py` (2 tests)

Remove:
- `class TestCephNfsClientProvides(testbase.TestBaseCharm)` from `test_ceph_nfs.py`
- `class TestCephRgwClientProviderHandler(testbase.TestBaseCharm)` from `test_ceph_rgw.py`

#### test_ceph_nfs.py — fixture replacing setUp's 16 patches

```python
import json
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch
import pytest
from ops import testing
import charm
from tests.unit.conftest import ceph_nfs_relation, peer_relation, default_network

PATCH_LIST = [
    ("ceph.check_output", "ceph_check_output"),
    ("ceph.create_fs_volume", "create_fs_volume"),
    ("ceph.get_named_key", "get_named_key"),
    ("ceph.remove_named_key", "remove_named_key"),
    ("ceph.get_osd_count", "get_osd_count"),
    ("ceph.list_fs_volumes", "list_fs_volumes"),
    ("ceph.list_mgr_modules", "list_mgr_modules"),
    ("ceph_broker.check_output", "broker_check_output"),
    ("microceph.enable_nfs", "enable_nfs"),
    ("microceph.disable_nfs", "disable_nfs"),
    ("microceph.microceph_has_service", "microceph_has_service"),
    ("microceph.subprocess.check_output", "microceph_check_output"),
    ("microceph_client.ClusterService.list_services", "list_services"),
    ("utils.get_fsid", "get_fsid"),
    ("utils.get_mon_addresses", "get_mon_addresses"),
    ("utils.run_cmd", "run_cmd"),
]

@pytest.fixture
def nfs_mocks():
    with ExitStack() as stack:
        mocks = {name: stack.enter_context(patch(path)) for path, name in PATCH_LIST}
        mocks["get_named_key"].return_value = "fa-key"
        mocks["list_services"].return_value = []
        mocks["list_fs_volumes"].return_value = []
        mocks["get_fsid"].return_value = "f00"
        mocks["get_mon_addresses"].return_value = ["foo.lish"]
        mocks["list_mgr_modules"].return_value = {"disabled_modules": [{"name": "microceph"}]}

        def _add_service(candidate, cluster_id, _):
            mocks["list_services"].return_value.append(
                {"service": "nfs", "group_id": cluster_id, "location": candidate})
        mocks["enable_nfs"].side_effect = _add_service

        def _remove_service(candidate, cluster_id):
            for svc in list(mocks["list_services"].return_value):
                if svc["service"] == "nfs" and svc["group_id"] == cluster_id and svc["location"] == candidate:
                    mocks["list_services"].return_value.remove(svc)
                    return
        mocks["disable_nfs"].side_effect = _remove_service

        def _add_fs_volume(volume_name):
            mocks["list_fs_volumes"].return_value.append({"name": volume_name})
        mocks["create_fs_volume"].side_effect = _add_fs_volume
        yield mocks
```

**Multi-step state chaining:** Multiple harness events = multiple `ctx.run()` calls. State from each run
can be passed as input to the next. Mocks persist across calls (same objects).

```python
def make_peer_rel(unit_numbers):
    """Build PeerRelation with remote peers."""
    return testing.PeerRelation(
        "peers",
        local_unit_data={"public-address": "10.0.0.10"},
        peers_data={n: {"public-address": f"pub-addr-{n}", f"microceph/{n}": f"foo{n}"}
                    for n in unit_numbers},
    )
```

**test_ensure_nfs_cluster** (most complex — multi-step):

```python
def test_ensure_nfs_cluster(nfs_mocks):
    ctx = testing.Context(charm.MicroCephCharm)

    # Step 1: NFS relation, no peers → no enable_nfs called
    nfs_rel = ceph_nfs_relation()
    state = testing.State(leader=True, relations=[nfs_rel], networks=[default_network()])
    with ctx(ctx.on.relation_changed(nfs_rel), state) as mgr:
        mgr.charm.ready_for_service = MagicMock(return_value=True)
        state_out = mgr.run()
    nfs_mocks["enable_nfs"].assert_not_called()

    # Step 2: Add peer unit 1 with services → NFS enabled on foo1
    nfs_mocks["list_services"].return_value = [
        {"service": "mon", "location": "foo1"}, {"service": "mgr", "location": "foo1"},
        {"service": "osd", "location": "foo1"}, {"service": "mds", "location": "foo1"},
    ]
    peer_rel = make_peer_rel([1])
    state2 = testing.State(leader=True, relations=[nfs_rel, peer_rel], networks=[default_network()])
    with ctx(ctx.on.relation_changed(peer_rel), state2) as mgr:
        mgr.charm.ready_for_service = MagicMock(return_value=True)
        state_out2 = mgr.run()
    nfs_mocks["enable_nfs"].assert_called_once_with("foo1", "manila-cephfs", "pub-addr-1")
    nfs_rel_out = state_out2.get_relation(nfs_rel.id)
    assert nfs_rel_out.local_app_data["client"] == "client.manila-cephfs"
    assert json.loads(nfs_rel_out.local_app_data["mon_hosts"]) == ["foo.lish"]
    # Continue adding units 2, 3 in further ctx.run() calls with updated make_peer_rel([1,2,3])
```

**test_remove_relation_rebalance** — use `ctx.on.relation_departed(rel)` to simulate relation removal.

#### test_ceph_rgw.py migration

```python
import json
from unittest.mock import MagicMock, patch
import pytest
from ops import testing
import ceph_rgw
import charm
from tests.unit.conftest import default_network

@pytest.fixture
def rgw_service_status():
    return {}

def test_ceph_rgw_connected_ready(rgw_service_status):
    rgw_service_status["rgw"] = {"9999": {}}
    with patch("ceph.get_osd_count", return_value=3), \
         patch("ceph.check_output"), \
         patch("utils.run_cmd", side_effect=lambda cmd: json.dumps(rgw_service_status)
               if cmd == ["sudo", "microceph.ceph", "service", "status"] else MagicMock()):
        ctx = testing.Context(charm.MicroCephCharm)
        rgw_rel = testing.Relation(ceph_rgw.CEPH_RGW_READY_RELATION,
                                   remote_app_name="consumer", remote_units_data={0: {"foo": "lish"}})
        state = testing.State(leader=True, config={"enable-rgw": "*"},
                              relations=[rgw_rel], networks=[default_network()])
        with ctx(ctx.on.relation_changed(rgw_rel), state) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=True)
            state_out = mgr.run()
        rel_out = state_out.get_relation(rgw_rel.id)
        assert rel_out.local_app_data.get("ready") == "true"

def test_set_readiness_on_related_units():
    """Multi-step: config changes and update_status events drive readiness transitions."""
    svc_status = {}
    with patch("utils.run_cmd", side_effect=lambda cmd: json.dumps(svc_status)
               if cmd == ["sudo", "microceph.ceph", "service", "status"] else MagicMock()) as run_cmd, \
         patch("ceph.get_osd_count") as get_osd_count, \
         patch("ceph.check_output"):
        ctx = testing.Context(charm.MicroCephCharm)
        rgw_rel = testing.Relation(ceph_rgw.CEPH_RGW_READY_RELATION,
                                   remote_app_name="consumer", remote_units_data={0: {"foo": "lish"}})

        # Step 1: enable-rgw="" → ready=false, no run_cmd
        state = testing.State(leader=True, config={"enable-rgw": ""},
                              relations=[rgw_rel], networks=[default_network()])
        with ctx(ctx.on.relation_changed(rgw_rel), state) as mgr:
            mgr.charm.ready_for_service = MagicMock(return_value=False)
            state_out = mgr.run()
        assert state_out.get_relation(rgw_rel.id).local_app_data.get("ready") == "false"
        run_cmd.assert_not_called()

        # Steps 2-5: enable-rgw="*", vary osd_count, pool_size, svc_status via update_status
        # In each step: state = testing.State(config={...}, unit_status=<prior status>, ...)
        # Final step: svc_status["rgw"] = {"9999": {}} → ready=true
```

**Verification:**
```bash
tox -e py3 -- tests/unit/test_ceph_nfs.py tests/unit/test_ceph_rgw.py -v
```

---

### Agent C — Migrate `test_relation_handlers.py` + `test_update_status_upgrade.py` + `test_storage_osd_config.py`

Remove:
- `class TestRelationHelpers(testbase.TestBaseCharm)` from `test_relation_handlers.py`
- `class TestUpdateStatusUpgradeReconcile(testbase.TestBaseCharm)` from `test_update_status_upgrade.py`
- `class TestConfigChangedOsdDevices(testbase.TestBaseCharm)` from `test_storage_osd_config.py`

#### test_relation_handlers.py (1 test)

```python
from unittest.mock import patch
import pytest
from ops import testing
import relation_handlers
import charm
from tests.unit.conftest import default_network


@patch("relation_handlers.gethostname")
def test_collect_peer_data(gethostname):
    gethostname.return_value = "test-hostname"
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = testing.PeerRelation(
        "peers",
        local_unit_data={"public-address": "10.0.0.10", "microceph/0": "test-hostname"},
    )
    state = testing.State(leader=True, relations=[peer_rel], networks=[default_network()])
    with ctx(ctx.on.relation_changed(peer_rel), state) as mgr:
        change_data = relation_handlers.collect_peer_data(mgr.charm.model)
    assert "microceph/0" not in change_data
    assert change_data["public-address"] == "10.0.0.10"

    gethostname.return_value = "changed-hostname"
    with ctx(ctx.on.relation_changed(peer_rel), state) as mgr:
        with pytest.raises(relation_handlers.HostnameChangeError):
            relation_handlers.collect_peer_data(mgr.charm.model)
```

#### test_update_status_upgrade.py (5 tests)

Key mapping: `harness.charm.status.set(BlockedStatus("msg"))` → `State(unit_status=BlockedStatus("msg"))`

```python
from unittest.mock import patch
import pytest
from ops import testing
from ops.model import ActiveStatus, BlockedStatus
import charm
import cluster
from tests.unit.conftest import default_network


def test_update_status_retries_pending_upgrade():
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(leader=True, networks=[default_network()])
    with patch.object(charm.cluster.ClusterUpgrades, "upgrade_requested", return_value=True), \
         patch.object(charm.MicroCephCharm, "handle_config_leader_charm_upgrade") as mock_upgrade, \
         patch.object(charm.MicroCephCharm, "ready_for_service",
                      new_callable=lambda: property(lambda self: True)):
        ctx.run(ctx.on.update_status(), state)
    mock_upgrade.assert_called_once()


def test_update_status_clears_stale_upgrade_health_blocked():
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(
        leader=True,
        unit_status=BlockedStatus(f"{cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN"),
        networks=[default_network()],
    )
    with patch.object(charm.cluster.ClusterUpgrades, "upgrade_requested", side_effect=[False, False]):
        state_out = ctx.run(ctx.on.update_status(), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_update_status_does_not_clear_unrelated_blocked_status():
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(
        leader=False,
        unit_status=BlockedStatus("waiting for something else"),
        networks=[default_network()],
    )
    with patch.object(charm.cluster.ClusterUpgrades, "upgrade_requested") as mock_requested:
        state_out = ctx.run(ctx.on.update_status(), state)
    assert state_out.unit_status.message == "waiting for something else"
    mock_requested.assert_not_called()


def test_update_status_does_not_clear_upgrade_health_blocked_when_upgrade_pending():
    snap_chan = "1.0/stable"
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(
        leader=False, config={"snap-channel": snap_chan},
        unit_status=BlockedStatus(f"{cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN"),
        networks=[default_network()],
    )
    with patch.object(charm.cluster.ClusterUpgrades, "upgrade_requested", return_value=True) as mock_requested:
        state_out = ctx.run(ctx.on.update_status(), state)
    assert cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX in state_out.unit_status.message
    mock_requested.assert_called_once_with(snap_chan)


def test_update_status_non_leader_clears_stale_upgrade_health_blocked():
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(
        leader=False,
        unit_status=BlockedStatus(f"{cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX}: HEALTH_WARN"),
        networks=[default_network()],
    )
    with patch.object(charm.cluster.ClusterUpgrades, "upgrade_requested", return_value=False), \
         patch.object(charm.MicroCephCharm, "handle_config_leader_charm_upgrade") as mock_handler:
        state_out = ctx.run(ctx.on.update_status(), state)
    assert isinstance(state_out.unit_status, ActiveStatus)
    mock_handler.assert_not_called()
```

#### test_storage_osd_config.py (15 tests)

All 15 tests call `_on_config_changed_osd_devices(event)` directly via Manager pattern.

Translations:
- `self.harness.update_config({...})` → `State(config={...})`
- `self.harness.charm.storage._stored.last_osd_devices = "..."` → `mgr.charm.storage._stored.last_osd_devices = "..."`
- `self._setup_ready_charm()` → set `mgr.charm.peers.interface.state.joined = True` and `mgr.charm.ready_for_service = MagicMock(return_value=True)`

```python
from unittest.mock import MagicMock, patch
import pytest
from ops import testing
import charm
from tests.unit.conftest import peer_relation, default_network


def test_empty_osd_devices_skips():
    ctx = testing.Context(charm.MicroCephCharm)
    state = testing.State(config={"osd-devices": ""}, networks=[default_network()])
    with ctx(ctx.on.config_changed(), state) as mgr:
        event = MagicMock()
        mgr.charm.storage._on_config_changed_osd_devices(event)
    event.defer.assert_not_called()


@patch("utils.subprocess")
def test_success_calls_add_osd_match(subprocess):
    ctx = testing.Context(charm.MicroCephCharm)
    peer_rel = peer_relation()
    state = testing.State(leader=True, config={"osd-devices": "eq(@type,'nvme')"},
                          relations=[peer_rel], networks=[default_network()])
    with ctx(ctx.on.config_changed(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.charm.ready_for_service = MagicMock(return_value=True)
        event = MagicMock()
        mgr.charm.storage._on_config_changed_osd_devices(event)
    subprocess.run.assert_called_with(
        ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')"],
        capture_output=True, text=True, check=True, timeout=180,
    )
# Apply this Manager pattern to all remaining 13 tests
```

**Verification:**
```bash
tox -e py3 -- tests/unit/test_relation_handlers.py tests/unit/test_update_status_upgrade.py tests/unit/test_storage_osd_config.py -v
```

---

## Wave 3: Final verification and cleanup (sequential)

1. **Run all tests:**
   ```bash
   tox -e py3
   ```
   Expected: all 81+ tests pass. Fix any failures.

2. **Clean up unused imports / dead code** left from migration.

3. **Lint:**
   ```bash
   tox -e lint
   ```

4. **Commit:**
   ```bash
   git add tests/unit/
   git commit -s -m "test: migrate all harness tests to ops-scenario"
   ```

---

## Important Notes for Agents

1. **conftest.py is auto-loaded by pytest** — `cos_agent_patched` autouse fixture applies to all `tests/unit/` functions.

2. **Import path** — use `from ops import testing` (not `import scenario` or `from scenario import ...`).

3. **Context is stateless** — create `testing.Context(charm.MicroCephCharm)` per-test or as fixture. After `ctx.run()`, `ctx.action_results` and `ctx.action_logs` hold per-run data.

4. **Multi-step tests** — multiple `harness.charm.on.event.emit()` calls = multiple `ctx.run()` calls, each with updated state built from prior `state_out`.

5. **StoredState via State** — set initial stored state with `State(stored_states=[testing.StoredState(owner_path="...", data={...})])`. Set during event via Manager: `mgr.charm.some_handler._stored.key = value`.

6. **Network addresses** — use `default_network()` from conftest to get `10.0.0.10` as unit address.

7. **Action results** — check `ctx.action_results` (dict) after run. Action failure raises `testing.ActionFailed` with `.message`.

8. **Manager pattern** for pre-dispatch charm mutation:
   ```python
   with ctx(ctx.on.config_changed(), state) as mgr:
       mgr.charm.some_attr = value  # mutate before dispatch
       state_out = mgr.run()
   ```
