#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Validators for OSD device configuration options."""

from dataclasses import dataclass

# Valid device-add-flags for Phase 1
VALID_FLAGS = frozenset(["wipe:osd", "encrypt:osd"])


@dataclass
class DeviceAddFlags:
    """Parsed device-add-flags."""

    wipe_osd: bool = False
    encrypt_osd: bool = False


def parse_device_add_flags(flags_str: str) -> DeviceAddFlags:
    """Parse the device-add-flags config option.

    Args:
        flags_str: Comma-separated flags like "wipe:osd,encrypt:osd"

    Returns:
        DeviceAddFlags with parsed boolean fields.

    Raises:
        ValueError: If an unknown flag is encountered.
    """
    result = DeviceAddFlags()

    if not flags_str or not flags_str.strip():
        return result

    for flag in flags_str.split(","):
        flag = flag.strip().lower()
        if not flag:
            continue
        if flag not in VALID_FLAGS:
            raise ValueError(
                f"Unknown flag: '{flag}'. Valid flags: {', '.join(sorted(VALID_FLAGS))}"
            )
        if flag == "wipe:osd":
            result.wipe_osd = True
        elif flag == "encrypt:osd":
            result.encrypt_osd = True

    return result
