from unittest.mock import patch

import pytest
from ops import testing

import charm
from .testbase import DUMMY_CA_CERT


@pytest.fixture(autouse=True)
def cos_agent_patched():
    with patch(
        "charms.ceph_mon.v0.ceph_cos_agent.CephCOSAgentProvider.__init__",
        return_value=None,
    ):
        yield


@pytest.fixture
def ctx() -> testing.Context:
    return testing.Context(charm.MicroCephCharm)


def peer_relation(**kwargs) -> testing.PeerRelation:
    return testing.PeerRelation(
        "peers",
        local_unit_data={"public-address": "10.0.0.10"},
        **kwargs,
    )


def default_network() -> testing.Network:
    return testing.Network(
        "juju-info",
        bind_addresses=[testing.BindAddress([testing.Address("10.0.0.10")])],
        ingress_addresses=["10.0.0.10"],
        egress_subnets=["10.0.0.0/24"],
    )


def identity_relation(secret_id: str) -> testing.Relation:
    return testing.Relation(
        "identity-service",
        remote_app_name="keystone",
        remote_app_data={
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
            "service-credentials": secret_id,
        },
    )


def identity_secret(relation_id: int) -> testing.Secret:
    return testing.Secret(
        id="secret:keystone-creds",
        contents={0: {"username": "svcuser1", "password": "svcpass1"}},
        owner="keystone",
        remote_grants={relation_id: {"microceph/0"}},
    )


def ingress_relation() -> testing.Relation:
    return testing.Relation(
        "traefik-route-rgw",
        remote_app_name="traefik",
        remote_app_data={"external_host": "dummy-ip", "scheme": "http"},
    )


def cert_transfer_relation() -> testing.Relation:
    return testing.Relation(
        "receive-ca-cert",
        remote_app_name="keystone",
        remote_units_data={0: {"ca": DUMMY_CA_CERT}},
    )


def ceph_nfs_relation(app_name: str = "manila-cephfs") -> testing.Relation:
    return testing.Relation(
        "ceph-nfs",
        remote_app_name=app_name,
        remote_units_data={0: {"foo": "lish"}},
    )


def ceph_remote_relation(relation_name: str = "remote-requirer") -> testing.Relation:
    return testing.Relation(
        relation_name,
        remote_app_name="remote-microceph",
        remote_app_data={
            "site-name": "secondary",
            "token": "eyJmc2lkIjoiNGM4Mzc1ZDYtMWNlZi00MzJhLWJkMTYtMDU3Y2I4YTJmNjdmIiwia2V5cmluZy5jbGllbnQucHJpbWFyeSI6IkFRQ2NPdjlvVUtLWE1CQUFLWlNBc25mSGgrMG95dkdjUEhEYzNBPT0iLCJtb24uaG9zdC53b3JrYm9vayI6IjE5Mi4xNjguMS41OSIsInB1YmxpY19uZXR3b3JrIjoiMTkyLjE2OC4xLjU5LzI0In0=",
        },
    )
