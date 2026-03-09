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

"""Shared constants for unit tests.

`TestBaseCharm`, `_MicroCephCharm`, and Harness helper methods were removed as part of
migrating charm-lifecycle unit tests from `ops.testing.Harness` to ops-scenario via
`ops[testing]`.

Pure Python tests (`test_ceph.py`, `test_microceph.py`, `test_broker.py`,
`test_device_flags.py`, and `test_utils.py`) are intentionally not migrated because
those tests validate pure Python functions and logic, not charm lifecycle behavior.
"""

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
