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

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_stable_order_regardless_of_api_order(self, client, get_live):
        # The same set of mons reported in different orders must serialize to the
        # same list, so a mere reordering never looks like a relation change
        # (LP#2161602).
        get_live.return_value = {"10.241.3.26", "10.241.3.45", "10.241.3.52"}

        client.from_socket.return_value.cluster.get_mon_addresses.return_value = [
            "10.241.3.26",
            "10.241.3.45",
            "10.241.3.52",
        ]
        first = utils.get_mon_addresses()

        client.from_socket.return_value.cluster.get_mon_addresses.return_value = [
            "10.241.3.52",
            "10.241.3.26",
            "10.241.3.45",
        ]
        second = utils.get_mon_addresses()

        self.assertEqual(first, second)
        self.assertEqual(first, ["10.241.3.26", "10.241.3.45", "10.241.3.52"])

    @patch("ceph.get_live_mon_ips")
    @patch("utils.Client")
    def test_stable_order_no_live_monmap(self, client, get_live):
        # Ordering is stabilised even on the fallback path (no live monmap).
        get_live.return_value = set()
        client.from_socket.return_value.cluster.get_mon_addresses.return_value = [
            "10.241.3.52",
            "10.241.3.26",
            "10.241.3.45",
        ]

        self.assertEqual(
            utils.get_mon_addresses(),
            ["10.241.3.26", "10.241.3.45", "10.241.3.52"],
        )


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


class TestSplitSpaceOrComma(unittest.TestCase):
    """Operators may delimit lists with spaces, commas, or both."""

    def test_split(self):
        cases = {
            "": [],
            "  ": [],
            "10.0.0.0/24": ["10.0.0.0/24"],
            "10.0.0.0/24,10.0.1.0/24": ["10.0.0.0/24", "10.0.1.0/24"],
            "10.0.0.0/24 10.0.1.0/24": ["10.0.0.0/24", "10.0.1.0/24"],
            "10.0.0.0/24, 10.0.1.0/24": ["10.0.0.0/24", "10.0.1.0/24"],
            " 10.0.0.0/24 ,, 10.0.1.0/24 ": ["10.0.0.0/24", "10.0.1.0/24"],
        }
        for value, expected in cases.items():
            self.assertEqual(utils.split_space_or_comma(value), expected, value)


class TestParseNetworks(unittest.TestCase):
    """parse_networks validates subnets and returns them canonicalised."""

    def test_valid_networks(self):
        cases = {
            "": [],
            "10.0.0.0/24": ["10.0.0.0/24"],
            "10.0.0.0/24 10.0.1.0/24": ["10.0.0.0/24", "10.0.1.0/24"],
            "10.0.0.0/24,10.0.1.0/24": ["10.0.0.0/24", "10.0.1.0/24"],
            "fd00::/64": ["fd00::/64"],
            "fd00:0:0:0::/64": ["fd00::/64"],  # canonicalised
        }
        for value, expected in cases.items():
            self.assertEqual(utils.parse_networks(value), expected, value)

    def test_order_preserved_and_deduped(self):
        """Entry order is the operator's priority; duplicates collapse.

        microceph picks the first listed subnet with a local address, so
        parse_networks must not reorder entries — only canonicalise and dedup.
        """
        cases = {
            # Operator order is preserved, not sorted.
            "10.0.1.0/24 10.0.0.0/24": ["10.0.1.0/24", "10.0.0.0/24"],
            "fd00::/64 10.0.0.0/24": ["fd00::/64", "10.0.0.0/24"],
            # Duplicates collapse to the first occurrence, respelled ones too.
            "10.0.0.0/24,10.0.0.0/24": ["10.0.0.0/24"],
            "fd00::/64 fd00:0:0:0::/64": ["fd00::/64"],
            "10.0.1.0/24 10.0.0.0/24 10.0.1.0/24": ["10.0.1.0/24", "10.0.0.0/24"],
        }
        for value, expected in cases.items():
            self.assertEqual(utils.parse_networks(value), expected, value)

    def test_bare_address_rejected(self):
        """ip_network() would accept a bare address as a /32 host route."""
        for value in ("10.0.0.1", "fd00::1", "10.0.0.0/24 10.0.1.1"):
            with self.assertRaises(ValueError) as ctx:
                utils.parse_networks(value)
            self.assertIn("no prefix length", str(ctx.exception))

    def test_host_bits_set_rejected(self):
        """An interface address is not a subnet: 10.0.0.5/24 has host bits set."""
        with self.assertRaises(ValueError) as ctx:
            utils.parse_networks("10.0.0.5/24")
        self.assertIn("10.0.0.5/24", str(ctx.exception))

    def test_malformed_entry_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            utils.parse_networks("10.0.0.0/24 not-a-subnet/24")
        self.assertIn("not-a-subnet/24", str(ctx.exception))
