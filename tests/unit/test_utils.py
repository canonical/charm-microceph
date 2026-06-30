# Copyright 2026 Canonical Ltd.
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

"""Tests for utils module."""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

import requests

import utils


class TestUtils(unittest.TestCase):

    @patch("utils.subprocess.run")
    def test_connected(self, mock_run):
        """Test snap_has_connection returns True when connected."""
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(utils.snap_has_connection("microceph.daemon", "dm-crypt"))
        mock_run.assert_called_once_with(
            ["snap", "run", "--shell", "microceph.daemon", "-c", "snapctl is-connected dm-crypt"],
            capture_output=True,
            text=True,
        )

    @patch("utils.subprocess.run")
    def test_not_connected(self, mock_run):
        """Test snap_has_connection returns False when not connected."""
        mock_run.return_value = MagicMock(returncode=1, stderr="")
        self.assertFalse(utils.snap_has_connection("microceph.daemon", "dm-crypt"))

    @patch("utils.subprocess.run")
    def test_error_with_stderr(self, mock_run):
        """Test snap_has_connection raises on unexpected error."""
        mock_run.return_value = MagicMock(returncode=1, stderr="snap not found", stdout="")
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            utils.snap_has_connection("microceph.daemon", "dm-crypt")
        self.assertEqual(ctx.exception.returncode, 1)
        self.assertEqual(ctx.exception.stderr, "snap not found")

    @patch("utils.subprocess.run")
    def test_unexpected_return_code(self, mock_run):
        """Test snap_has_connection raises on unexpected return code."""
        mock_run.return_value = MagicMock(returncode=2, stderr="unknown error", stdout="")
        with self.assertRaises(subprocess.CalledProcessError) as ctx:
            utils.snap_has_connection("microceph.daemon", "dm-crypt")
        self.assertEqual(ctx.exception.returncode, 2)


class TestGetMonAddresses(unittest.TestCase):
    """get_mon_addresses must cross-check the live monmap to drop dead mons.

    The microceph service API is not refreshed when a mon leaves the cluster
    out-of-band, so it can advertise dead mons. The published list is
    cross-checked against the live monmap (ceph mon dump).
    """

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_filters_dead_mons(self, client, get_live):
        # The service API still reports a mon that has left the cluster.
        client.from_socket.return_value.cluster.get_mon_addresses.return_value = [
            "10.0.0.1",
            "10.0.0.2",
            "10.0.0.3",
        ]
        get_live.return_value = {"10.0.0.1", "10.0.0.2"}  # .3 is dead

        self.assertEqual(utils.get_mon_addresses(), ["10.0.0.1", "10.0.0.2"])

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_no_live_monmap_returns_full_list(self, client, get_live):
        # If the live monmap is unavailable, never drop addresses.
        addrs = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
        client.from_socket.return_value.cluster.get_mon_addresses.return_value = addrs
        get_live.return_value = set()

        self.assertEqual(utils.get_mon_addresses(), addrs)

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_empty_intersection_falls_back_to_full_list(self, client, get_live):
        # A format mismatch (no overlap) must not regress to an empty list.
        addrs = ["10.0.0.1", "10.0.0.2"]
        client.from_socket.return_value.cluster.get_mon_addresses.return_value = addrs
        get_live.return_value = {"9.9.9.9"}

        self.assertEqual(utils.get_mon_addresses(), addrs)

    @patch("ceph.get_live_mon_ips")
    @patch("utils.microceph.get_mon_public_addresses")
    @patch("utils.Client")
    def test_api_failure_uses_legacy_then_filters(self, client, legacy, get_live):
        # API down -> legacy ceph.conf parse, still cross-checked against monmap.
        client.from_socket.return_value.cluster.get_mon_addresses.side_effect = (
            requests.HTTPError()
        )
        legacy.return_value = ["10.0.0.1", "10.0.0.2"]
        get_live.return_value = {"10.0.0.1"}

        self.assertEqual(utils.get_mon_addresses(), ["10.0.0.1"])

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_empty_api_list_does_not_warn_or_crash(self, client, get_live):
        # API returns nothing yet; with a live monmap the result is still [] and
        # the "removed all addresses" warning must not fire (nothing was removed).
        client.from_socket.return_value.cluster.get_mon_addresses.return_value = []
        get_live.return_value = {"10.0.0.1"}

        with self.assertNoLogs(utils.logger, level="WARNING"):
            self.assertEqual(utils.get_mon_addresses(), [])

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_ipv6_representation_mismatch_still_matches(self, client, get_live):
        # The API reports a non-canonical IPv6; the monmap is canonical. The
        # cross-check compares by value, so both addresses are kept (and the
        # original API strings are returned unchanged).
        addrs = ["fd00:0:0:0:0:0:0:1", "fd00::2"]
        client.from_socket.return_value.cluster.get_mon_addresses.return_value = addrs
        get_live.return_value = {"fd00::1", "fd00::2"}

        self.assertEqual(utils.get_mon_addresses(), addrs)


class TestNormalizeIp(unittest.TestCase):
    """_normalize_ip canonicalises bare IPs and leaves anything else untouched."""

    def test_normalize_ip(self):
        cases = {
            "10.0.0.1": "10.0.0.1",
            "fd00::1": "fd00::1",
            "fd00:0:0:0:0:0:0:1": "fd00::1",  # non-canonical IPv6 -> canonical
            "10.0.0.1:6789": "10.0.0.1:6789",  # has a port: not a bare IP, unchanged
            "not-an-ip": "not-an-ip",
            "": "",
        }
        for addr, expected in cases.items():
            self.assertEqual(utils._normalize_ip(addr), expected, addr)
