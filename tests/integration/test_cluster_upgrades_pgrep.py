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

"""Integration test: reproduce the pgrep false-positive upgrade block.

Deploys microceph and triggers a snap-channel change to exercise the
``perform_upgrade`` process guard.

Known bugs reproduced here:
- The current regex matches ``ceph-osd``, ``ceph-mon``, ``ceph-mgr``,
  and ``ceph-mds`` persistent daemon binaries.
- When RGW is enabled (via identity-service), ``radosgw`` also matches.
"""

import logging
import re

import jubilant
import pytest

from tests import helpers

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.smoke, pytest.mark.regression]

APP_NAME = "microceph"
LOOP_OSD_SPEC = "1G,1"
DEFAULT_TIMEOUT = 2400

CURRENT_REGEX = r"/snap/microceph/.*/(microceph|ceph|rados|rbd)"

DAEMON_BINARIES = ("ceph-osd", "ceph-mon", "ceph-mgr", "ceph-mds", "radosgw")

CANNOT_UPGRADE_MSG = "Cannot upgrade, one of microceph|ceph|rados|rbd commands is running"


@pytest.fixture(scope="module")
def deployed_microceph(juju: jubilant.Juju, microceph_charm) -> str:
    helpers.deploy_microceph(
        juju,
        microceph_charm,
        APP_NAME,
        loop_osd_spec=LOOP_OSD_SPEC,
        timeout=DEFAULT_TIMEOUT,
    )
    helpers.wait_for_ceph_health_ok(juju, APP_NAME, timeout=DEFAULT_TIMEOUT)
    return APP_NAME


def _pgrep_lines(juju: jubilant.Juju, unit: str, pattern: str) -> list[str]:
    result = juju.ssh(unit, "pgrep", "-af", pattern, check=False)
    return [line.strip() for line in result.splitlines() if line.strip()]


def _ps_lines(juju: jubilant.Juju, unit: str) -> list[str]:
    result = juju.ssh(unit, "ps", "aux", "--no-headers", check=False)
    return [line.strip() for line in result.splitlines() if line.strip()]


def test_current_regex_matches_daemons(juju: jubilant.Juju, deployed_microceph: str):
    """Verify the current regex matches persistent daemon binaries.

    These are NOT transient CLI commands -- matching them is a bug.
    """
    unit = helpers.first_unit_name(juju.status(), APP_NAME)
    lines = _pgrep_lines(juju, unit, CURRENT_REGEX)

    assert lines, "expected at least one process to match the current regex"
    logger.info("Current regex matched:\n  %s", "\n  ".join(lines))

    for name in DAEMON_BINARIES:
        matched = any(name in line for line in lines)
        if matched:
            logger.info("Daemon %s: MATCHED (false positive)", name)


def test_upgrade_should_not_block_on_daemons(juju: jubilant.Juju, deployed_microceph: str):
    """A snap-channel change should NOT block on persistent daemon binaries.

    The current regex matches ceph-osd, ceph-mon, and ceph-mgr which are
    always-running daemons, not transient CLI commands. This test asserts
    the *correct* behavior and is expected to FAIL until the regex is fixed.
    """
    unit = helpers.first_unit_name(juju.status(), APP_NAME)

    current_channel = str(juju.config(APP_NAME).get("snap-channel", ""))
    track = current_channel.split("/")[0] if "/" in current_channel else current_channel
    candidate = f"{track}/candidate"
    if candidate == current_channel:
        candidate = f"{track}/edge"

    snap_info = juju.ssh(unit, "snap", "info", "microceph", check=False)
    if f"{candidate}:" not in snap_info:
        pytest.skip(f"Channel {candidate} not available for microceph snap")

    logger.info("Channel transition: %s -> %s", current_channel, candidate)

    try:
        juju.config(APP_NAME, {"snap-channel": candidate})

        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, APP_NAME, timeout=600)

        app_status = juju.status().apps[APP_NAME]
        msg = app_status.app_status.message

        # Correct behavior: daemon processes should NOT block the upgrade
        assert (
            CANNOT_UPGRADE_MSG not in msg
        ), f"should not be blocked by daemon processes, got: {msg!r}"
    finally:
        juju.config(APP_NAME, {"snap-channel": current_channel})
        with helpers.fast_forward(juju):
            helpers.wait_for_apps(juju, APP_NAME, timeout=DEFAULT_TIMEOUT)


def test_fixed_regex_matches_subset_of_current(juju: jubilant.Juju, deployed_microceph: str):
    r"""The \b-guarded fixed regex matches a subset of the current one."""
    unit = helpers.first_unit_name(juju.status(), APP_NAME)
    lines = _ps_lines(juju, unit)

    current_pat = re.compile(CURRENT_REGEX)
    fixed_pat = re.compile(r"/snap/microceph/.*/(microceph|ceph|rados|rbd)\b")

    current_matched = [line for line in lines if current_pat.search(line)]
    fixed_matched = [line for line in lines if fixed_pat.search(line)]

    logger.info(
        "ps lines matched: current=%d, fixed=%d",
        len(current_matched),
        len(fixed_matched),
    )
    assert len(fixed_matched) <= len(current_matched)

    for line in fixed_matched:
        assert current_pat.search(line), f"fixed regex matched a line current did not: {line!r}"


def test_fixed_regex_still_catches_cli_paths(juju: jubilant.Juju, deployed_microceph: str):
    r"""The \b-fixed regex still matches microceph/ceph/rados/rbd process paths."""
    unit = helpers.first_unit_name(juju.status(), APP_NAME)
    lines = _ps_lines(juju, unit)
    fixed_pat = re.compile(r"/snap/microceph/.*/(microceph|ceph|rados|rbd)\b")

    matched = [line for line in lines if fixed_pat.search(line)]
    logger.info("Fixed regex matched %d ps lines", len(matched))
    assert len(matched) >= 1, "fixed regex should match at least some processes"
