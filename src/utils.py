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

import requests

import microceph
from microceph_client import Client

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


def get_mon_addresses():
    """Get the Ceph mon addresses."""
    client = Client.from_socket()
    try:
        return client.cluster.get_mon_addresses()
    except requests.HTTPError:
        logger.debug("Mon api call failed, fall back to legacy method")
        return microceph.get_mon_public_addresses()


def get_fsid():
    """Get the FSID from ceph.conf."""
    with open("/var/snap/microceph/current/conf/ceph.conf", "r") as f:
        for line in f:
            if line.startswith("fsid") and "=" in line:
                return line.split("=")[1].strip()
