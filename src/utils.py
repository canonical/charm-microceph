#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
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

"""Utils module."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def run_cmd(cmd: list) -> str:
    """Execute provided command via subprocess."""
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=180)
        logger.debug(f"Command {' '.join(cmd)} finished; Output: {process.stdout}")
        return process.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed executing cmd: {cmd}, error: {e.stderr}")
        raise e
