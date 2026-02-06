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

import ops_sunbeam.test_utils as test_utils
from unit import testbase

import charm


class TestConfigChangedOsdDevices(testbase.TestBaseCharm):
    """Tests for the osd-devices config-changed handler."""

    PATCHES = ["subprocess"]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
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

    def _setup_ready_charm(self):
        """Set up peer relation and mock ready_for_service to return True."""
        test_utils.add_complete_peer_relation(self.harness)
        self.harness._charm.peers.interface.state.joined = True
        self.harness.charm.ready_for_service = MagicMock(return_value=True)

    def _call_handler(self, event=None):
        """Call the osd-devices config-changed handler with a mock event."""
        if event is None:
            event = MagicMock()
        self.harness.charm.storage._on_config_changed_osd_devices(event)
        return event

    # --- Skip when not configured ---

    def test_empty_osd_devices_skips(self):
        """Handler returns early when osd-devices is empty."""
        self.harness.update_config({"osd-devices": ""})
        event = self._call_handler()
        event.defer.assert_not_called()

    def test_whitespace_osd_devices_skips(self):
        """Handler returns early when osd-devices is whitespace."""
        self.harness.update_config({"osd-devices": "   "})
        event = self._call_handler()
        event.defer.assert_not_called()

    @patch("utils.subprocess")
    def test_unchanged_config_skips_snap_call(self, subprocess):
        """Handler skips snap call when osd-devices config is unchanged."""
        self._setup_ready_charm()
        self.harness.update_config(
            {"osd-devices": "eq(@type,'nvme')", "device-add-flags": "wipe:osd"}
        )
        self.harness.charm.storage._stored.last_osd_devices = "eq(@type,'nvme')"
        self.harness.charm.storage._stored.last_wipe_osd = True
        self.harness.charm.storage._stored.last_encrypt_osd = False

        subprocess.run.reset_mock()
        self._call_handler()

        subprocess.run.assert_not_called()

    def test_empty_osd_devices_resets_config_cache(self):
        """Handler clears cached config when osd-devices is empty."""
        self.harness.charm.storage._stored.last_osd_devices = "eq(@type,'nvme')"
        self.harness.charm.storage._stored.last_wipe_osd = True
        self.harness.charm.storage._stored.last_encrypt_osd = True
        self.harness.update_config({"osd-devices": "", "device-add-flags": "wipe:osd"})

        self._call_handler()

        self.assertEqual(self.harness.charm.storage._stored.last_osd_devices, "")
        self.assertFalse(self.harness.charm.storage._stored.last_wipe_osd)
        self.assertFalse(self.harness.charm.storage._stored.last_encrypt_osd)

    # --- Defer when not ready ---

    @patch("microceph.is_ready", return_value=False)
    def test_not_ready_defers(self, _is_ready):
        """Handler defers when cluster is not ready."""
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})
        event = self._call_handler()
        event.defer.assert_called_once()

    # --- Successful OSD enrollment ---

    @patch("utils.subprocess")
    def test_success_calls_add_osd_match(self, subprocess):
        """Handler calls microceph disk add with the DSL expression."""
        self._setup_ready_charm()
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        self._call_handler()

        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')"],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    @patch("utils.subprocess")
    def test_success_with_wipe_flag(self, subprocess):
        """Handler passes --wipe when wipe:osd flag is set."""
        self._setup_ready_charm()
        self.harness.update_config(
            {"osd-devices": "eq(@type,'nvme')", "device-add-flags": "wipe:osd"}
        )

        self._call_handler()

        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')", "--wipe"],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    @patch("utils.subprocess")
    def test_success_with_encrypt_flag(self, subprocess):
        """Handler passes --encrypt when encrypt:osd flag is set."""
        self._setup_ready_charm()
        self.harness.update_config(
            {"osd-devices": "eq(@type,'nvme')", "device-add-flags": "encrypt:osd"}
        )

        self._call_handler()

        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')", "--encrypt"],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    @patch("utils.subprocess")
    def test_success_with_all_flags(self, subprocess):
        """Handler passes both --wipe and --encrypt when both flags are set."""
        self._setup_ready_charm()
        self.harness.update_config(
            {"osd-devices": "eq(@type,'nvme')", "device-add-flags": "wipe:osd,encrypt:osd"}
        )

        self._call_handler()

        subprocess.run.assert_called_with(
            [
                "microceph",
                "disk",
                "add",
                "--osd-match",
                "eq(@type,'nvme')",
                "--wipe",
                "--encrypt",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    @patch("utils.subprocess")
    def test_strips_whitespace_from_dsl(self, subprocess):
        """Handler strips leading/trailing whitespace from the DSL expression."""
        self._setup_ready_charm()
        self.harness.update_config({"osd-devices": "  eq(@type,'nvme')  "})

        self._call_handler()

        subprocess.run.assert_called_with(
            ["microceph", "disk", "add", "--osd-match", "eq(@type,'nvme')"],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )

    # --- No devices matched (not an error) ---

    @patch("utils.subprocess")
    def test_no_devices_matched_stays_active(self, subprocess):
        """Handler does not defer or crash when snap reports no devices matched."""
        self._setup_ready_charm()
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        subprocess.CalledProcessError = CalledProcessError
        subprocess.run.side_effect = CalledProcessError(
            returncode=1, cmd=["microceph"], stderr="Error: no devices matched the expression"
        )

        event = self._call_handler()
        event.defer.assert_not_called()

    # --- CalledProcessError (real error) ---

    @patch("utils.subprocess")
    def test_snap_error_does_not_crash(self, subprocess):
        """Handler handles snap failure without unhandled exception."""
        self._setup_ready_charm()
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        subprocess.CalledProcessError = CalledProcessError
        subprocess.run.side_effect = CalledProcessError(
            returncode=1, cmd=["microceph"], stderr="Error: some snap failure"
        )

        event = self._call_handler()
        event.defer.assert_not_called()

    @patch("utils.subprocess")
    def test_snap_error_with_empty_stderr(self, subprocess):
        """Handler handles CalledProcessError with empty stderr without crash."""
        self._setup_ready_charm()
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        subprocess.CalledProcessError = CalledProcessError
        subprocess.run.side_effect = CalledProcessError(returncode=1, cmd=["microceph"], stderr="")

        event = self._call_handler()
        event.defer.assert_not_called()

    @patch("utils.subprocess")
    def test_snap_timeout_does_not_crash(self, subprocess):
        """Handler handles snap timeout without unhandled exception."""
        self._setup_ready_charm()
        self.harness.update_config({"osd-devices": "eq(@type,'nvme')"})

        subprocess.CalledProcessError = CalledProcessError
        subprocess.TimeoutExpired = TimeoutExpired
        subprocess.run.side_effect = TimeoutExpired(cmd=["microceph"], timeout=180)

        event = self._call_handler()
        event.defer.assert_not_called()

    # --- Invalid device-add-flags ---

    @patch("utils.subprocess")
    def test_invalid_flags_does_not_call_snap(self, subprocess):
        """Handler does not call snap when device-add-flags are invalid."""
        self._setup_ready_charm()
        self.harness.update_config(
            {"osd-devices": "eq(@type,'nvme')", "device-add-flags": "invalid:flag"}
        )

        self._call_handler()
        subprocess.run.assert_not_called()
