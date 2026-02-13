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
