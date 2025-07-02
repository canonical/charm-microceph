# Copyright 2025 Canonical Ltd.
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

from ops.testing import Harness
from ops_sunbeam import test_utils

import charm

DUMMY_CA_CERT = """-----BEGIN CERTIFICATE-----
MIIDdzCCAl+gAwIBAgIUexFR59kb53PwxGKCFFO32jHAGKwwDQYJKoZIhvcNAQEL
BQAwSzELMAkGA1UEBhMCSU4xCzAJBgNVBAgMAkFQMQwwCgYDVQQHDANWVFoxITAf
BgNVBAoMGEludGVybmV0IFdpZGdpdHMgUHR5IEx0ZDAeFw0yNDA2MjMwNTQ2Mjha
Fw0yOTA2MjIwNTQ2MjhaMEsxCzAJBgNVBAYTAklOMQswCQYDVQQIDAJBUDEMMAoG
A1UEBwwDVlRaMSEwHwYDVQQKDBhJbnRlcm5ldCBXaWRnaXRzIFB0eSBMdGQwggEi
MA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCUmJ0xjPppm0YV8hPQjbZH9+LO
LU8HXUb2EYU9yb+UEP24grGar2zsVUBXWGJAXIAYejyDapSRjYoCnPECRHfrCqs2
vhZmQzPII+6Nllf3IpzS65TEfssfEtiSweN2sXLPymHaRKcq+rnmmpOM3vO396pc
COJX7WG/+qDJUhJthdbA008sKulG4Qq7NGaUA6Y4IMlZsZFEMp17rvFWNRSZBPVd
qrmW38v7rZfJwHrN4NL0me/1GZ+9ucnXnD5q/D1kRURt8J8cbFrPqGo4QwTzoNIi
D8Q7yRHUIMDY2MGmtpwzluh1HYg97IRJO0ciVXGL1yKEpELJ2Q32jS4xx2GJAgMB
AAGjUzBRMB0GA1UdDgQWBBSNg6SlHP06mM/vFPUoM9p37ZbUvzAfBgNVHSMEGDAW
gBSNg6SlHP06mM/vFPUoM9p37ZbUvzAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3
DQEBCwUAA4IBAQCGHlGuKr4L7nfZgFY1VZI14pSUvEZKPIXb4jPMvsVIdQY8wowM
9TDFmsDps0W+XZDNq5wwRtWiVKoNO6zw9ZKVlsKas4hnhqnaWD101xI9xN/ADax1
OHmBVcugXeYdWxmaz3JdiVKmwhiscmAiAWr4MS2FY/moZAl/U+YeIxbCxqKkZgJF
sEygfjVGcGUYrPvBB3SIyL+n8N9anht7u6ZY1chw6dnlT79mcx4huNE+NCSRK+7t
aU6GF5joUr0UWjFkoXpINM+ozet/bYvxa8MJ5OvSeU1ahHFeOmv0axs0JHvV0rW4
I7bWFePvjNsCPUyBSGu3GCisT5/FaxcS/IOA
-----END CERTIFICATE-----
"""


class _MicroCephCharm(charm.MicroCephCharm):
    """MicroCeph test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)

    def configure_ceph(self, event):
        return True


class TestBaseCharm(test_utils.CharmTestCase):
    def add_complete_identity_relation(self, harness: Harness) -> None:
        """Add complete identity-service relation."""
        credentials_content = {"username": "svcuser1", "password": "svcpass1"}
        credentials_id = harness.add_model_secret("keystone", credentials_content)
        app_data = {
            "admin-domain-id": "admindomid1",
            "admin-project-id": "adminprojid1",
            "admin-user-id": "adminuserid1",
            "api-version": "3",
            "auth-host": "keystone.local",
            "auth-port": "12345",
            "auth-protocol": "http",
            "internal-host": "keystone.internal",
            "internal-port": "5000",
            "internal-protocol": "http",
            "internal-auth-url": "http://keystone.internal/v3",
            "service-domain": "servicedom",
            "service-domain_id": "svcdomid1",
            "service-host": "keystone.service",
            "service-port": "5000",
            "service-protocol": "http",
            "service-project": "svcproj1",
            "service-project-id": "svcprojid1",
            "service-credentials": credentials_id,
        }

        # Cannot use ops add_relation [1] directly due to secrets
        # [1] https://ops.readthedocs.io/en/latest/#ops.testing.Harness.add_relation
        rel_id = test_utils.add_base_identity_service_relation(harness)
        harness.grant_secret(credentials_id, harness.charm.app.name)
        harness.update_relation_data(rel_id, "keystone", app_data)

    def add_complete_ingress_relation(self, harness: Harness) -> None:
        """Add complete traefik-route relations."""
        harness.add_relation(
            "traefik-route-rgw",
            "traefik",
            app_data={"external_host": "dummy-ip", "scheme": "http"},
        )

    def add_complete_peer_relation(self, harness: Harness, unit_data=None) -> None:
        """Add complete peer relation data."""
        unit_data = unit_data or {"public-address": "dummy-ip"}
        rel_id = harness.add_relation("peers", harness.charm.app.name, unit_data=unit_data)
        return rel_id

    def add_complete_certificate_transfer_relation(self, harness: Harness) -> None:
        """Add complete certificate_transfer relation."""
        harness.add_relation("receive-ca-cert", "keystone", unit_data={"ca": DUMMY_CA_CERT})

    def add_cos_agent_integration(self, harness: Harness) -> None:
        """Add cos agent integration."""
        harness.add_relation("cos-agent", harness.charm.app.name)

    def add_ceph_nfs_relation(self, harness: Harness, app_name="manila-cephfs") -> int:
        """Add ceph-nfs-client relation."""
        return harness.add_relation("ceph-nfs", app_name, unit_data={"foo": "lish"})

    def add_unit(self, harness: Harness, rel_id: int, unit_name: str, unit_data={}) -> None:
        harness.add_relation_unit(rel_id, unit_name)
        harness.update_relation_data(rel_id, unit_name, unit_data)
