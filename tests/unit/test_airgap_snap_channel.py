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

"""Regression tests for airgapped snap-channel handling.

Bug: when the snap-channel config was changed in a strictly airgapped
environment (one that uses a snap store proxy for snap installs but has no
route to the public internet), the leader unit went into an error state.

Root cause: to validate the requested snap track, the charm made a *direct*
outbound HTTPS request to the public Snapcraft API (``api.snapcraft.io``)
instead of asking the local snapd, which is configured to use the store
proxy. In an airgapped env that request failed with a ConnectionError and the
exception propagated out of the config-changed / update-status hook.

Fix: query snapd's ``find`` endpoint (via ``snap.SnapClient``) over its Unix
socket. snapd honours the system snap store proxy configuration, so the track
lookup succeeds anywhere snap installs already work.

These tests pin the proxy-aware behaviour: the track lookup must go through
snapd and must not touch ``requests`` / ``api.snapcraft.io``.
"""

import unittest
from unittest.mock import patch

import requests
from unit import testbase

import charm as charm_module
import microceph

# A representative snapd `find` (GET /v2/find) response for microceph, matching
# the shape snapd returns over its Unix socket. snapd routes this query through
# the configured store proxy.
SNAPD_FIND_RESULT = {
    "name": "microceph",
    "channel": "squid/stable",
    "version": "19.2.3+snapcf306793a4",
    "tracks": ["squid", "latest", "tentacle", "reef", "quincy"],
    "channels": {
        "latest/stable": {"version": "18.2.4+snapc9f2b08f92", "channel": "latest/stable"},
        "squid/stable": {"version": "19.2.3+snapcf306793a4", "channel": "squid/stable"},
        "tentacle/edge": {"version": "20.2.1+snapb8f87722ee", "channel": "tentacle/edge"},
    },
}


def _network_unreachable(*args, **kwargs):
    """Emulate an airgapped host with no route to the public snapstore."""
    raise requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.snapcraft.io', port=443): "
        "Max retries exceeded (Network is unreachable)"
    )


class TestAirgapSnapChannelUnit(unittest.TestCase):
    """Track lookups must go through snapd, not the public snapstore."""

    @patch("requests.get", side_effect=_network_unreachable)
    @patch("microceph.snap.SnapClient")
    def test_get_snap_info_uses_snapd(self, mock_snap_client, mock_requests_get):
        """get_snap_info asks snapd (proxy-aware) rather than api.snapcraft.io."""
        client = mock_snap_client.return_value
        client.get_snap_information.return_value = SNAPD_FIND_RESULT

        result = microceph.get_snap_info("microceph")

        self.assertEqual(result, SNAPD_FIND_RESULT)
        client.get_snap_information.assert_called_once_with("microceph")
        # The whole point of the fix: no direct call to the public snapstore.
        mock_requests_get.assert_not_called()

    @patch("requests.get", side_effect=_network_unreachable)
    @patch("microceph.snap.SnapClient")
    def test_get_snap_tracks_from_snapd(self, mock_snap_client, _mock_requests_get):
        """Tracks are derived from snapd's top-level ``tracks`` list."""
        mock_snap_client.return_value.get_snap_information.return_value = SNAPD_FIND_RESULT

        tracks = microceph.get_snap_tracks("microceph")

        self.assertEqual(tracks, {"squid", "latest", "tentacle", "reef", "quincy"})

    @patch("requests.get", side_effect=_network_unreachable)
    @patch("microceph.snap.SnapClient")
    def test_can_upgrade_snap_when_airgapped(self, mock_snap_client, _mock_requests_get):
        """The track check succeeds via snapd even with no public route."""
        mock_snap_client.return_value.get_snap_information.return_value = SNAPD_FIND_RESULT

        # e.g. moving from squid/edge to squid/stable, as in the bug report.
        self.assertTrue(microceph.can_upgrade_snap("squid", "squid"))
        # And a forward move to a newer series still validates.
        self.assertTrue(microceph.can_upgrade_snap("squid", "tentacle"))

    @patch("requests.get", side_effect=_network_unreachable)
    @patch("microceph.snap.SnapClient")
    def test_can_upgrade_from_latest_resolves_via_snapd(
        self, mock_snap_client, _mock_requests_get
    ):
        """Resolving a current 'latest' track reads the version from snapd."""
        mock_snap_client.return_value.get_snap_information.return_value = SNAPD_FIND_RESULT

        # latest/stable is reef (18.x) here, so upgrading to squid is allowed
        # and downgrading to quincy is not.
        self.assertTrue(microceph.can_upgrade_snap("latest", "squid"))
        self.assertFalse(microceph.can_upgrade_snap("latest", "quincy"))


class TestAirgapSnapChannelCharm(testbase.TestBaseCharm):
    """End-to-end: a snap-channel change no longer errors the leader unit."""

    def setUp(self):
        super().setUp(charm_module, [])
        self.init_harness()
        self.harness.set_leader()

    @patch("requests.get", side_effect=_network_unreachable)
    @patch("microceph.snap.SnapClient")
    def test_can_upgrade_charm_payload_airgapped(self, mock_snap_client, _mock_requests_get):
        """can_upgrade_charm_payload succeeds airgapped by consulting snapd."""
        mock_snap_client.return_value.get_snap_information.return_value = SNAPD_FIND_RESULT
        unit_charm = self.harness.charm

        with (
            patch.object(unit_charm, "ready_for_service", return_value=True),
            patch("cluster.CephStatus") as mock_status,
        ):
            mock_status.return_value.ceph_health.return_value = (
                charm_module.cluster.CephHealth.Ok,
                "",
            )
            # Mirror the bug report's squid/edge -> squid/stable: a same-track
            # move whose validation used to hit api.snapcraft.io. The default
            # channel track is 'tentacle', so validate a tentacle/stable move.
            ok, msg = unit_charm.cluster_upgrades.can_upgrade_charm_payload("tentacle/stable")

        self.assertTrue(ok, msg)


if __name__ == "__main__":
    unittest.main()
