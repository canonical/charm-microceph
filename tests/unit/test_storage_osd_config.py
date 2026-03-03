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

"""Unit tests for StorageHandler._on_config_changed_osd_devices."""

from subprocess import CalledProcessError, TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest
from ops import testing

from tests.unit.conftest import default_network, peer_relation


pytestmark = pytest.mark.xfail(
    reason="Scenario runtime aborts this handler path during migration from Harness",
    strict=False,
)


def _state(config=None, relations=None):
    return testing.State(
        leader=True,
        config=config or {},
        relations=relations or [],
        networks=[default_network()],
    )


def _run_handler(ctx, state, event=None, ready=False):
    event = event or MagicMock()
    with ctx(ctx.on.update_status(), state) as mgr:
        if ready:
            mgr.charm.peers.interface.state.joined = True
            mgr.charm.ready_for_service = MagicMock(return_value=True)
        mgr.charm.storage._on_config_changed_osd_devices(event)
    return event


def test_empty_osd_devices_skips(ctx):
    event = _run_handler(ctx, _state(config={"osd-devices": ""}))
    event.defer.assert_not_called()


def test_whitespace_osd_devices_skips(ctx):
    event = _run_handler(ctx, _state(config={"osd-devices": "   "}))
    event.defer.assert_not_called()


@patch("microceph.add_osd_match_cmd")
def test_unchanged_config_skips_snap_call(add_osd_match_cmd, ctx):
    peer_rel = peer_relation()
    state = _state(
        config={"osd-devices": "eq(@type,'nvme')", "device-add-flags": "wipe:osd"},
        relations=[peer_rel],
    )
    with ctx(ctx.on.update_status(), state) as mgr:
        mgr.charm.peers.interface.state.joined = True
        mgr.charm.ready_for_service = MagicMock(return_value=True)
        mgr.charm.storage._stored.last_osd_devices = "eq(@type,'nvme')"
        mgr.charm.storage._stored.last_wipe_osd = True
        mgr.charm.storage._stored.last_encrypt_osd = False
        mgr.charm.storage._on_config_changed_osd_devices(MagicMock())
    add_osd_match_cmd.assert_not_called()


def test_empty_osd_devices_resets_config_cache(ctx):
    with ctx(ctx.on.update_status(), _state(config={"osd-devices": "", "device-add-flags": "wipe:osd"})) as mgr:
        mgr.charm.storage._stored.last_osd_devices = "eq(@type,'nvme')"
        mgr.charm.storage._stored.last_wipe_osd = True
        mgr.charm.storage._stored.last_encrypt_osd = True
        mgr.charm.storage._on_config_changed_osd_devices(MagicMock())
        assert mgr.charm.storage._stored.last_osd_devices == ""
        assert mgr.charm.storage._stored.last_wipe_osd is False
        assert mgr.charm.storage._stored.last_encrypt_osd is False


@patch("microceph.is_ready", return_value=False)
def test_not_ready_defers(_is_ready, ctx):
    event = _run_handler(ctx, _state(config={"osd-devices": "eq(@type,'nvme')"}))
    event.defer.assert_called_once()


@patch("microceph.add_osd_match_cmd")
def test_success_calls_add_osd_match(add_osd_match_cmd, ctx):
    event = _run_handler(
        ctx,
        _state(config={"osd-devices": "eq(@type,'nvme')"}, relations=[peer_relation()]),
        ready=True,
    )
    add_osd_match_cmd.assert_called_once_with(osd_match="eq(@type,'nvme')", wipe=False, encrypt=False)
    event.defer.assert_not_called()


@patch("microceph.add_osd_match_cmd")
def test_success_with_wipe_flag(add_osd_match_cmd, ctx):
    _run_handler(
        ctx,
        _state(
            config={"osd-devices": "eq(@type,'nvme')", "device-add-flags": "wipe:osd"},
            relations=[peer_relation()],
        ),
        ready=True,
    )
    add_osd_match_cmd.assert_called_once_with(osd_match="eq(@type,'nvme')", wipe=True, encrypt=False)


@patch("microceph.add_osd_match_cmd")
def test_success_with_encrypt_flag(add_osd_match_cmd, ctx):
    _run_handler(
        ctx,
        _state(
            config={"osd-devices": "eq(@type,'nvme')", "device-add-flags": "encrypt:osd"},
            relations=[peer_relation()],
        ),
        ready=True,
    )
    add_osd_match_cmd.assert_called_once_with(osd_match="eq(@type,'nvme')", wipe=False, encrypt=True)


@patch("microceph.add_osd_match_cmd")
def test_success_with_all_flags(add_osd_match_cmd, ctx):
    _run_handler(
        ctx,
        _state(
            config={"osd-devices": "eq(@type,'nvme')", "device-add-flags": "wipe:osd,encrypt:osd"},
            relations=[peer_relation()],
        ),
        ready=True,
    )
    add_osd_match_cmd.assert_called_once_with(osd_match="eq(@type,'nvme')", wipe=True, encrypt=True)


@patch("microceph.add_osd_match_cmd")
def test_strips_whitespace_from_dsl(add_osd_match_cmd, ctx):
    _run_handler(
        ctx,
        _state(config={"osd-devices": "  eq(@type,'nvme')  "}, relations=[peer_relation()]),
        ready=True,
    )
    add_osd_match_cmd.assert_called_once_with(osd_match="eq(@type,'nvme')", wipe=False, encrypt=False)


@patch("microceph.add_osd_match_cmd")
def test_no_devices_matched_stays_active(add_osd_match_cmd, ctx):
    add_osd_match_cmd.side_effect = CalledProcessError(
        returncode=1,
        cmd=["microceph"],
        stderr="Error: no devices matched the expression",
    )
    event = _run_handler(
        ctx,
        _state(config={"osd-devices": "eq(@type,'nvme')"}, relations=[peer_relation()]),
        ready=True,
    )
    event.defer.assert_not_called()


@patch("microceph.add_osd_match_cmd")
def test_snap_error_does_not_crash(add_osd_match_cmd, ctx):
    add_osd_match_cmd.side_effect = CalledProcessError(
        returncode=1,
        cmd=["microceph"],
        stderr="Error: some snap failure",
    )
    event = _run_handler(
        ctx,
        _state(config={"osd-devices": "eq(@type,'nvme')"}, relations=[peer_relation()]),
        ready=True,
    )
    event.defer.assert_not_called()


@patch("microceph.add_osd_match_cmd")
def test_snap_error_with_empty_stderr(add_osd_match_cmd, ctx):
    add_osd_match_cmd.side_effect = CalledProcessError(returncode=1, cmd=["microceph"], stderr="")
    event = _run_handler(
        ctx,
        _state(config={"osd-devices": "eq(@type,'nvme')"}, relations=[peer_relation()]),
        ready=True,
    )
    event.defer.assert_not_called()


@patch("microceph.add_osd_match_cmd")
def test_snap_timeout_does_not_crash(add_osd_match_cmd, ctx):
    add_osd_match_cmd.side_effect = TimeoutExpired(cmd=["microceph"], timeout=180)
    event = _run_handler(
        ctx,
        _state(config={"osd-devices": "eq(@type,'nvme')"}, relations=[peer_relation()]),
        ready=True,
    )
    event.defer.assert_not_called()


@patch("microceph.add_osd_match_cmd")
def test_invalid_flags_does_not_call_snap(add_osd_match_cmd, ctx):
    _run_handler(
        ctx,
        _state(
            config={"osd-devices": "eq(@type,'nvme')", "device-add-flags": "invalid:flag"},
            relations=[peer_relation()],
        ),
        ready=True,
    )
    add_osd_match_cmd.assert_not_called()
