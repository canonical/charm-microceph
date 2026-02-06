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

"""Unit tests for device-add-flags parsing."""

import unittest

from device_flags import (
    DeviceAddFlags,
    parse_device_add_flags,
)


class TestDeviceAddFlagsParsing(unittest.TestCase):
    """Tests for parse_device_add_flags function."""

    def test_empty_flags(self):
        """Empty string returns default flags."""
        flags = parse_device_add_flags("")
        self.assertFalse(flags.wipe_osd)
        self.assertFalse(flags.encrypt_osd)

    def test_none_flags(self):
        """None returns default flags."""
        flags = parse_device_add_flags(None)
        self.assertFalse(flags.wipe_osd)
        self.assertFalse(flags.encrypt_osd)

    def test_whitespace_only(self):
        """Whitespace-only string returns default flags."""
        flags = parse_device_add_flags("   ")
        self.assertFalse(flags.wipe_osd)
        self.assertFalse(flags.encrypt_osd)

    def test_wipe_osd_flag(self):
        """wipe:osd flag is parsed correctly."""
        flags = parse_device_add_flags("wipe:osd")
        self.assertTrue(flags.wipe_osd)
        self.assertFalse(flags.encrypt_osd)

    def test_encrypt_osd_flag(self):
        """encrypt:osd flag is parsed correctly."""
        flags = parse_device_add_flags("encrypt:osd")
        self.assertFalse(flags.wipe_osd)
        self.assertTrue(flags.encrypt_osd)

    def test_multiple_flags(self):
        """Multiple flags are parsed correctly."""
        flags = parse_device_add_flags("wipe:osd,encrypt:osd")
        self.assertTrue(flags.wipe_osd)
        self.assertTrue(flags.encrypt_osd)

    def test_multiple_flags_reversed_order(self):
        """Order of flags doesn't matter."""
        flags = parse_device_add_flags("encrypt:osd,wipe:osd")
        self.assertTrue(flags.wipe_osd)
        self.assertTrue(flags.encrypt_osd)

    def test_flags_with_spaces(self):
        """Spaces around flags are handled."""
        flags = parse_device_add_flags("  wipe:osd  ,  encrypt:osd  ")
        self.assertTrue(flags.wipe_osd)
        self.assertTrue(flags.encrypt_osd)

    def test_case_insensitive(self):
        """Flag parsing is case-insensitive."""
        flags = parse_device_add_flags("WIPE:OSD")
        self.assertTrue(flags.wipe_osd)

    def test_mixed_case(self):
        """Mixed case flags are handled."""
        flags = parse_device_add_flags("Wipe:OSD,ENCRYPT:osd")
        self.assertTrue(flags.wipe_osd)
        self.assertTrue(flags.encrypt_osd)

    def test_invalid_flag(self):
        """Unknown flag raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            parse_device_add_flags("invalid:flag")
        self.assertIn("Unknown flag", str(ctx.exception))
        self.assertIn("invalid:flag", str(ctx.exception))

    def test_invalid_flag_mixed_with_valid(self):
        """Mix of valid and invalid flags raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            parse_device_add_flags("wipe:osd,invalid:flag")
        self.assertIn("Unknown flag", str(ctx.exception))

    def test_phase2_flags_not_supported(self):
        """Phase 2 flags (wipe:wal, etc.) are not supported yet."""
        for flag in ["wipe:wal", "wipe:db", "encrypt:wal", "encrypt:db"]:
            with self.assertRaises(
                ValueError, msg=f"Flag {flag} should not be supported in Phase 1"
            ) as ctx:
                parse_device_add_flags(flag)
            self.assertIn("Unknown flag", str(ctx.exception))

    def test_empty_flag_in_list(self):
        """Empty flag in comma-separated list is ignored."""
        flags = parse_device_add_flags("wipe:osd,,encrypt:osd")
        self.assertTrue(flags.wipe_osd)
        self.assertTrue(flags.encrypt_osd)

    def test_returns_dataclass(self):
        """Result is a DeviceAddFlags dataclass."""
        flags = parse_device_add_flags("wipe:osd")
        self.assertIsInstance(flags, DeviceAddFlags)
