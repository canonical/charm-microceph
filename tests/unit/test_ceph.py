# Copyright 2024 Canonical Ltd.
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

"""Tests for the ceph module."""

import json
import unittest
from unittest.mock import patch

import ceph


class TestCeph(unittest.TestCase):
    @patch.object(ceph, "check_output")
    @patch("socket.gethostname")
    def test_remove_named_key(self, gethostname, check_output):
        gethostname.return_value = "foo"

        ceph.remove_named_key("icious")

        cmd = [
            "microceph.ceph",
            "--name",
            "mon.",
            "--keyring",
            f"{ceph.VAR_LIB_CEPH}/mon/ceph-foo/keyring",
            "auth",
            "del",
            "icious",
        ]
        check_output.assert_called_once_with(cmd)

    @patch("ceph.check_output")
    def test_cluster_has_quorum(self, check_output):
        check_output.return_value = b'{"quorum": [ 0 ]}'
        self.assertTrue(ceph.cluster_has_quorum())

    @patch("ceph.check_output")
    def test_cluster_has_quorum_uses_microceph_ceph(self, check_output):
        """Verify cluster_has_quorum calls microceph.ceph, not bare ceph."""
        check_output.return_value = b'{"quorum": [ 0, 1, 2 ]}'
        ceph.cluster_has_quorum()
        check_output.assert_called_once_with(["microceph.ceph", "status", "--format=json"])

    @patch("ceph.check_output")
    def test_cluster_has_quorum_no_quorum(self, check_output):
        check_output.return_value = b'{"quorum": []}'
        self.assertFalse(ceph.cluster_has_quorum())

    @patch("ceph.check_output")
    def test_cluster_has_quorum_command_failure(self, check_output):
        from subprocess import CalledProcessError

        check_output.side_effect = CalledProcessError(1, "cmd")
        self.assertFalse(ceph.cluster_has_quorum())

    @patch("utils.run_cmd")
    def test_create_fs_volume(self, run_cmd):
        ceph.create_fs_volume("volly")

        run_cmd.assert_called_once_with(["microceph.ceph", "fs", "volume", "create", "volly"])

    @patch("utils.run_cmd")
    def test_list_fs_volumes(self, run_cmd):
        volume = {"name": "volly"}
        run_cmd.return_value = json.dumps([volume])

        fs_volumes = ceph.list_fs_volumes()

        run_cmd.assert_called_once_with(["microceph.ceph", "fs", "volume", "ls"])
        self.assertEqual(fs_volumes, [volume])

    @patch("ceph.check_output")
    def test_get_live_mon_ips(self, check_output):
        """get_live_mon_ips parses bare IPs out of the live monmap."""
        dump = {
            "mons": [
                {
                    "name": "node1",
                    "public_addr": "10.0.0.1:6789/0",
                    "public_addrs": {
                        "addrvec": [
                            {"type": "v2", "addr": "10.0.0.1:3300", "nonce": 0},
                            {"type": "v1", "addr": "10.0.0.1:6789", "nonce": 0},
                        ]
                    },
                },
                {
                    "name": "node2",
                    "public_addr": "10.0.0.2:6789/0",
                    "public_addrs": {
                        "addrvec": [{"type": "v2", "addr": "10.0.0.2:3300", "nonce": 0}]
                    },
                },
            ],
            "quorum": [0, 1],
        }
        check_output.return_value = json.dumps(dump).encode("UTF-8")

        self.assertEqual(ceph.get_live_mon_ips(), {"10.0.0.1", "10.0.0.2"})
        check_output.assert_called_once_with(["microceph.ceph", "mon", "dump", "--format", "json"])

    @patch("ceph.check_output")
    def test_get_live_mon_ips_handles_failure(self, check_output):
        """A failed mon dump yields an empty set so callers fall back safely."""
        from subprocess import CalledProcessError

        check_output.side_effect = CalledProcessError(1, "cmd")
        self.assertEqual(ceph.get_live_mon_ips(), set())

    @patch("ceph.check_output")
    def test_get_live_mon_ips_ipv6(self, check_output):
        """Bracketed IPv6 messenger addresses are reduced to the bare IP."""
        dump = {
            "mons": [
                {
                    "name": "node1",
                    "public_addr": "[fd00::1]:6789/0",
                    "public_addrs": {
                        "addrvec": [
                            {"type": "v2", "addr": "[fd00::1]:3300", "nonce": 0},
                            {"type": "v1", "addr": "[fd00::1]:6789", "nonce": 0},
                        ]
                    },
                },
                {
                    "name": "node2",
                    "public_addr": "[fd00::2]:6789/0",
                    "public_addrs": {
                        "addrvec": [{"type": "v2", "addr": "[fd00::2]:3300", "nonce": 0}]
                    },
                },
            ],
        }
        check_output.return_value = json.dumps(dump).encode("UTF-8")

        self.assertEqual(ceph.get_live_mon_ips(), {"fd00::1", "fd00::2"})

    @patch("ceph.check_output")
    def test_get_live_mon_ips_non_json(self, check_output):
        """A non-JSON mon dump (e.g. a warning prefix) yields an empty set."""
        check_output.return_value = b"WARNING: noise\nnot json"
        self.assertEqual(ceph.get_live_mon_ips(), set())

    @patch("ceph.check_output")
    def test_get_live_mon_ips_non_dict_json(self, check_output):
        """Valid but non-object JSON (e.g. `null`) yields an empty set, not a crash."""
        check_output.return_value = b"null"
        self.assertEqual(ceph.get_live_mon_ips(), set())

    def test_addr_to_ip(self):
        """_addr_to_ip parses all messenger forms and canonicalises the result."""
        cases = {
            "10.0.0.1:6789/0": "10.0.0.1",
            "10.0.0.1:3300": "10.0.0.1",
            "10.0.0.1": "10.0.0.1",
            "[fd00::1]:6789/0": "fd00::1",
            "[fd00::1]:3300": "fd00::1",
            "fd00::1": "fd00::1",
            "fd00:0:0:0:0:0:0:1": "fd00::1",  # non-canonical IPv6 -> canonical
            "": "",
            "garbage": "",
            "[bad::v6": "",
        }
        for addr, expected in cases.items():
            self.assertEqual(ceph._addr_to_ip(addr), expected, addr)
