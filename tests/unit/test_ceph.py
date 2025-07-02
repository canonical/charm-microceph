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

    @patch("ceph.os")
    @patch("ceph.socket")
    @patch("ceph.check_output")
    def test_is_quorum(self, check_output, _skt, os):
        check_output.return_value = b'{"state": "peon"}'
        os.path.exists.return_value = True
        self.assertTrue(ceph.is_quorum())

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
