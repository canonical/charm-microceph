#!/usr/bin/env python3

# Copyright 2023 Canonical Ltd.
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


"""Microceph Operator Charm.

This charm deploys and manages microceph.
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from socket import gethostname
from subprocess import CalledProcessError, TimeoutExpired
from typing import List

import netifaces
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from charms.ceph_mon.v0 import ceph_cos_agent
from charms.operator_libs_linux.v2 import snap
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus

import ceph
import cluster
import maintenance
import microceph
import utils
from ceph_nfs import CephNfsProviderHandler
from ceph_rgw import CEPH_RGW_READY_RELATION, CephRgwProviderHandler
from microceph_adopt_ceph import AdoptCephRequiresHandler
from microceph_client import ClusterServiceUnavailableException, UnrecognizedClusterConfigOption
from microceph_remote import MicroCephRemoteHandler
from radosgw import RadosGWHandler
from relation_handlers import (
    CephClientProviderHandler,
    CephMdsProviderHandler,
    CephRadosGWProviderHandler,
    MicroClusterNewNodeEvent,
    MicroClusterNodeAddedEvent,
    MicroClusterPeerHandler,
    UpgradeNodeDoneEvent,
    UpgradeNodeRequestEvent,
    collect_peer_data,
)
from storage import StorageHandler

logger = logging.getLogger(__name__)
CACERT_FILE = "/usr/local/share/ca-certificates/receive-keystone-ca-bundle.crt"
MAX_PG_PER_OSD = 400
PUBLIC_NETWORK_CONFIG = "ceph-public-network"
CLUSTER_NETWORK_CONFIG = "ceph-cluster-network"


class MicroCephCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "microceph"
    storage = None  # StorageHandler

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)

        # Initialise Modules.
        self.storage = StorageHandler(self)
        self.cluster_nodes = cluster.ClusterNodes(self)
        self.cluster_upgrades = cluster.ClusterUpgrades(self)
        self.maintenance = maintenance.Maintenance(self)
        self.rgw = RadosGWHandler(self)
        self.cos_agent = ceph_cos_agent.CephCOSAgentProvider(
            self,
            refresh_cb=microceph.cos_agent_refresh_cb,
            departed_cb=microceph.cos_agent_departed_cb,
            is_ready_cb=self.ready_for_service,
            mgr_config_set_cb=microceph.cos_agent_mgr_config_set_cb,
            is_mgr_available_cb=microceph.cos_agent_is_mgr_available_cb,
        )

        # Initialise handlers for events.
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.set_pool_size_action, self._set_pool_size_action)
        self.framework.observe(self.on.peers_relation_created, self._on_peer_relation_created)
        self.framework.observe(self.on["peers"].relation_departed, self._on_peer_relation_departed)

    def _on_install(self, event: ops.framework.EventBase) -> None:
        config = self.model.config.get
        cmd = [
            "snap",
            "install",
            "microceph",
            "--channel",
            config("snap-channel"),
        ]
        utils.run_cmd(cmd, timeout=900)

        cmd = ["sudo", "snap", "alias", "microceph.ceph", "ceph"]
        utils.run_cmd(cmd)

        try:
            snap.SnapCache()["microceph"].hold()
        except Exception:
            logger.exception("Failed to hold microceph refresh: ")

        self.channel = self.model.config.get("snap-channel")

    def _is_benign_cluster_remove_error(self, error: CalledProcessError | TimeoutExpired) -> bool:
        """Return True if a cluster-remove failure is safe to ignore during teardown."""
        stderr = error.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        else:
            stderr = str(stderr)

        if "Cannot leave a cluster with 1 members" in stderr:
            return True

        if "not found in dqlite or database" in stderr:
            return True

        if "cluster member" in stderr.lower() and "not found" in stderr.lower():
            return True

        return False

    def _on_stop(self, event: ops.StopEvent):
        """Removes departing unit from the MicroCeph cluster forcefully."""
        hostname = gethostname()

        # Whole-application teardown: when the entire application is removed quorum
        # can be lost if nodes race - skip cleanup
        if utils.is_departing(self.app, context="stop"):
            logger.info("Application is being removed; skipping cluster removal for %s", hostname)
            return

        try:
            if microceph.cluster_member_count() <= 1:
                logger.info("Skipping cluster removal for last MicroCeph member %s", hostname)
                return
        except Exception as e:
            logger.warning("Could not determine MicroCeph cluster size during stop: %s", e)

        self._remove_self(hostname)

    def _remove_self(self, hostname: str) -> None:
        """Forcefully remove this host from the cluster, tolerating benign errors."""
        try:
            microceph.remove_cluster_member(hostname, is_force=True)
        except (CalledProcessError, TimeoutExpired) as e:
            if self._is_benign_cluster_remove_error(e):
                logger.info("Ignoring benign cluster removal error during stop: %s", e.stderr)
                return

            # NOTE: Depending upon the state of the cluster, forcefully removing
            # a host may result in errors even if the request was successful.
            if microceph.is_cluster_member(hostname):
                raise e

    def _on_update_status(self, event: ops.framework.EventBase) -> None:
        """Update status event handler."""
        if utils.is_departing(self.app):
            logger.info("Application is being removed; skipping update-status")
            return

        snap_chan = self.model.config.get("snap-channel")
        # Cleanup can run on all units, including units that are no longer leader.
        self._clear_resolved_upgrade_blocked_status(snap_chan)

        if not self.unit.is_leader():
            logger.debug(f"Unit {self.unit.name} is not leader, skipping rgw readiness update")
            return

        self._reconcile_pending_upgrade(snap_chan)

        if not self.ready_for_service():
            logger.debug("Microceph not ready for service, skipping rgw readiness update")
            return

        if not self.model.relations.get(CEPH_RGW_READY_RELATION):
            logger.debug("No ceph-rgw-ready relation, skipping rgw readiness update")
            return

        self.ceph_rgw.set_readiness_on_related_units()

    def _reconcile_pending_upgrade(self, snap_chan: str) -> None:
        """Retry pending upgrade checks on leader when an upgrade is still pending."""
        if not self.cluster_upgrades.upgrade_requested(snap_chan):
            return

        if not self.ready_for_service():
            logger.debug(
                "Microceph not ready for service, skipping pending upgrade reconciliation"
            )
            return

        with sunbeam_guard.guard(self, "Checking pending charm upgrades"):
            self.handle_config_leader_charm_upgrade()

        self._clear_resolved_upgrade_blocked_status(snap_chan)

    def _clear_resolved_upgrade_blocked_status(self, snap_chan: str) -> None:
        """Clear stale blocked status once a health-gated upgrade has recovered."""
        status = self.status.status
        if not isinstance(status, BlockedStatus):
            return

        if not status.message.startswith(cluster.UPGRADE_HEALTH_BLOCKED_MSG_PREFIX):
            return

        if self.cluster_upgrades.upgrade_requested(snap_chan):
            logger.debug("Upgrade still pending, keeping blocked health status")
            return

        logger.info("Clearing stale blocked status from resolved Ceph health warning")
        self.status.set(ActiveStatus(""))

    def _on_peer_relation_created(self, event: ops.framework.EventBase) -> None:
        logging.debug(f"Peer relation created: {event}")
        self.peers.set_unit_data(collect_peer_data(self.model))

    def _on_peer_relation_departed(self, event: ops.framework.EventBase) -> None:
        self.handle_traefik_ready(event)

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Reconcile the charm, unless the application is being torn down.

        A departing unit must not reconcile: the cluster loses quorum during
        teardown, so the cluster calls made here would error or hang. Gating the
        main reconcile entry point keeps a departing unit inert.

        Config checks run here rather than in _on_config_changed so every
        reconcile path re-enforces them: relation handlers call configure_charm
        directly, and finishing a reconcile sets the unit active, which would
        otherwise silently clear the blocked status of a unit whose config is
        still in violation.
        """
        if utils.is_departing(self.app):
            logger.info("Application is being removed; skipping reconcile")
            return
        try:
            self.check_configs()
        except sunbeam_guard.BlockedExceptionError as e:
            logger.warning("Charm is blocked: %s", e.msg)
            self.status.set(e.to_status())
            return
        super().configure_charm(event)

    def check_configs(self) -> None:
        """Validate config options and enforce the immutable ones.

        Runs on every unit, so a violation blocks the whole application rather
        than only the leader.

        :raises BlockedExceptionError: on a malformed subnet, or on a change to
            an option that cannot be applied after bootstrap.
        """
        self._check_immutable_config(
            "namespace-projects", self.model.config.get("namespace-projects"), "deployment"
        )
        # Validate both network options on every unit, so a malformed subnet
        # blocks before any cluster operation is attempted.
        self._get_network_config(CLUSTER_NETWORK_CONFIG)
        public_net = self._get_network_config(PUBLIC_NETWORK_CONFIG)
        # microceph cannot set public_network on a bootstrapped cluster, so a
        # post-bootstrap change can only be reported, never applied. The
        # OPTION VALUE consumed at bootstrap ("" when the space-subnet
        # fallback was used) is recorded by the bootstrap and adopt paths;
        # any later change to the option blocks — including setting it to
        # the subnet in effect, which the charm cannot verify against a
        # running cluster.
        # Compare as a set: subnet order is a microceph address-selection
        # priority, but reordering an unchanged set is not a network change.
        self._check_immutable_config(
            PUBLIC_NETWORK_CONFIG,
            public_net,
            "bootstrap",
            normalize=lambda value: set(utils.split_space_or_comma(value)),
        )

    def _check_immutable_config(self, key: str, current, applied_at: str, normalize=None) -> None:
        """Block if an immutable config option differs from its recorded value.

        :param current: the option's current value, canonicalised the same way
            as the recorded one.
        :param applied_at: when the recorded value was fixed, for the message.
        :param normalize: optional transform applied to both sides before
            comparing, for values with more than one equivalent spelling.
        :raises BlockedExceptionError: if a value was recorded and differs.
        """
        recorded = self.leader_get(key)
        if recorded is None:
            return
        applied = json.loads(recorded)
        if normalize is None:
            changed = applied != current
        else:
            changed = normalize(applied) != normalize(current)
        if changed:
            raise sunbeam_guard.BlockedExceptionError(
                f"Config {key} cannot be changed after {applied_at}, revert to {applied!r}"
            )

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Run configuration on this unit."""
        super().configure_unit(event)
        self._handle_receive_ca_cert(event)

    def _on_config_changed(self, event: ops.framework.EventBase) -> None:
        with sunbeam_guard.guard(self, "Checking configs"):
            if not self.is_valid_placement_directive(self.model.config.get("enable-rgw")):
                raise sunbeam_guard.BlockedExceptionError("Improper value for config enable-rgw")

            # check_configs also runs inside configure_charm, so every
            # reconcile path re-enforces it. Calling it here first lets a
            # violation bail out of this guard before the peer-data publish
            # below, so a later failure there cannot replace the specific
            # blocked message with a generic one.
            self.check_configs()
            self.configure_charm(event)

            # Refresh peer data on config-changed so binding-derived addresses
            # (e.g. nfs-address) are published or cleared as config changes,
            # without waiting for a peers relation event.
            if self.model.get_relation("peers"):
                self.peers.set_unit_data(collect_peer_data(self.model))

    def _handle_receive_ca_cert(self, event: ops.framework.EventBase) -> None:
        contexts = self.contexts()
        cacert_path = Path(CACERT_FILE)
        if getattr(contexts.receive_ca_cert, "ca_bundle", None):
            cacert_in_bytes = contexts.receive_ca_cert.ca_bundle.encode()
            if cacert_path.exists():
                cert_from_file = cacert_path.read_bytes()
                if cert_from_file == cacert_in_bytes:
                    # CA Cert already exists with same content
                    logger.debug("CA cert exists with same content")
                    return

            # Write CACert if file does not exist or ca cert content is modified
            logger.debug("Write CA Cert file to /usr/local/share/ca-certificates/")
            cacert_path.write_bytes(contexts.receive_ca_cert.ca_bundle.encode())
            cacert_path.chmod(0o640)
        else:
            try:
                cacert_path.unlink()
            except FileNotFoundError:
                # No CA Cert file to unlink
                # No need to run update-ca-certificates
                logger.debug("No CA Cert to delete, ignore")
                return

        try:
            logger.debug("Running update-ca-certificates to update the certs")
            subprocess.run(["update-ca-certificates"], check=True)
            if self.model.config.get("enable-rgw") == "*":
                self.configure_rgw_service(event)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise sunbeam_guard.BlockedExceptionError("Updating CA Certificates failed")

    def is_valid_placement_directive(self, directive: str) -> bool:
        """Check if placement directive is valid or not."""
        supported_directives = ["*", ""]
        return directive in supported_directives

    def _set_pool_size_action(self, event: ops.framework.EventBase) -> None:
        """Set the size for one or more pools."""
        pools = event.params.get("pools")
        size = event.params.get("size")

        try:
            microceph.set_pool_size(pools, size)
            event.set_results({"status": "success"})
        except subprocess.CalledProcessError:
            logger.warning("Failed to set new pool size")
            event.set_results({"message": "set-pool-size failed"})
            event.fail()

    @property
    def channel(self) -> str:
        """Get the saved snap channel."""
        c = self.peers.get_app_data("channel")
        if c:
            return c
        # return default channel if not set.
        return self.model.config["snap-channel"]

    @channel.setter
    def channel(self, value: str) -> None:
        if self.unit.is_leader():
            logger.debug(f"Setting channel on peers rel: {value}")
            self.peers.set_app_data({"channel": value})

    @property
    def rgw_port(self):
        """Port used for RGW service."""
        return 80

    @property
    def service_endpoints(self):
        """Describe the swift/s3 service endpoints."""
        ingress_url = None
        try:
            if self.traefik_route_rgw and self.traefik_route_rgw.ready:
                scheme = self.traefik_route_rgw.interface.scheme
                external_host = self.traefik_route_rgw.interface.external_host
                ingress_url = f"{scheme}://{external_host}"
        except (AttributeError, KeyError):
            pass

        if not ingress_url:
            return []

        namespace_projects = self.leader_get("namespace-projects")
        if namespace_projects and json.loads(namespace_projects):
            swift_url = f"{ingress_url}/swift/v1/AUTH_$(project_id)s"
        else:
            swift_url = f"{ingress_url}/swift/v1"

        return [
            {
                "service_name": "swift",
                "type": "object-store",
                "description": "Object Store service",
                "internal_url": swift_url,
                "public_url": swift_url,
                "admin_url": f"{ingress_url}/swift/v1",
            },
            {
                "service_name": "s3",
                "type": "s3",
                "description": "s3 service",
                "internal_url": f"{ingress_url}",
                "public_url": f"{ingress_url}",
                "admin_url": f"{ingress_url}",
            },
        ]

    @property
    def traefik_config(self) -> dict:
        """Config to publish to traefik."""
        model = self.model.name
        app = "rgw"

        router_cfg = {
            f"juju-{model}-{app}-router": {
                "rule": "PathPrefix(`/`)",
                "service": f"juju-{model}-{app}-service",
                "entryPoints": ["web"],
            },
            f"juju-{model}-{app}-router-tls": {
                "rule": "PathPrefix(`/`)",
                "service": f"juju-{model}-{app}-service",
                "entryPoints": ["websecure"],
                "tls": {},
            },
        }

        # Note: Currently rgw, mon services are enabled on all storage nodes.
        # So get RGW IPs from mon ip addresses
        # https://github.com/canonical/microceph/issues/368
        # Get host key value from all units
        ips = self.peers.get_all_unit_values(key="public-address", include_local_unit=True)
        # ips = microceph.get_mon_public_addresses()
        rgw_lb_servers = [{"url": f"http://{ip}:{self.rgw_port}"} for ip in ips]
        health_check = {"path": "/swift/healthcheck", "scheme": "http"}
        service_cfg = {
            f"juju-{model}-{app}-service": {
                "loadBalancer": {"servers": rgw_lb_servers, "healthCheck": health_check}
            },
        }

        config = {
            "http": {
                "routers": router_cfg,
                "services": service_cfg,
            },
        }
        return config

    def get_relation_handlers(self, handlers=None) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []

        relation_handlers = {
            "adopt-ceph": (
                "adopt_ceph",
                lambda: AdoptCephRequiresHandler(self, "adopt-ceph", self.handle_ceph_adopt),
            ),
            "remote-provider": (
                "remote_provider",
                lambda: MicroCephRemoteHandler(self, "remote-provider", self.handle_rh_cb_noop),
            ),
            "remote-requirer": (
                "remote_requirer",
                lambda: MicroCephRemoteHandler(self, "remote-requirer", self.handle_rh_cb_noop),
            ),
            "peers": (
                "peers",
                lambda: MicroClusterPeerHandler(
                    self,
                    "peers",
                    self.configure_charm,
                    "peers" in self.mandatory_relations,
                ),
            ),
            "ceph": (
                "ceph",
                lambda: CephClientProviderHandler(self, "ceph", self.handle_rh_cb_noop),
            ),
            "radosgw": (
                "radosgw",
                lambda: CephRadosGWProviderHandler(self, self.handle_rh_cb_noop),
            ),
            "mds": (
                "mds",
                lambda: CephMdsProviderHandler(self, self.handle_rh_cb_noop),
            ),
            "ceph-nfs": (
                "ceph_nfs",
                lambda: CephNfsProviderHandler(self, "ceph-nfs", self.handle_rh_cb_noop),
            ),
            CEPH_RGW_READY_RELATION: (
                "ceph_rgw",
                lambda: CephRgwProviderHandler(self, CEPH_RGW_READY_RELATION),
            ),
            "traefik-route-rgw": (
                "traefik_route_rgw",
                lambda: sunbeam_rhandlers.TraefikRouteHandler(
                    self,
                    "traefik-route-rgw",
                    self.handle_traefik_ready,
                    "traefik-route-rgw" in self.mandatory_relations,
                ),
            ),
            "identity-service": (
                "id_svc",
                lambda: sunbeam_rhandlers.IdentityServiceRequiresHandler(
                    self,
                    "identity-service",
                    self.configure_charm,
                    self.service_endpoints,
                    self.model.config["region"],
                    "identity-service" in self.mandatory_relations,
                ),
            ),
        }

        for relation_name, (attr_name, handler_factory) in relation_handlers.items():
            if self.can_add_handler(relation_name, handlers):
                logger.debug(f"Adding relation handler for {relation_name}")
                try:
                    handler = handler_factory()
                    setattr(self, attr_name, handler)
                    handlers.append(handler)
                    if relation_name == "peers":
                        handler.upgrade_callback = self.upgrade_dispatch
                except AttributeError:
                    if relation_name == "identity-service":
                        logger.exception(
                            f"Application config `region` not set, skipping {relation_name} relation handler"
                        )
                    else:
                        raise

        # calling the superclass method to get any additional handlers not set by the charm.
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def ready_for_service(self) -> bool:
        """Check if service is ready or not."""
        if not microceph.is_ready():
            logger.warning("microceph snap not ready for service yet.")
            return False

        # ready for service if leader has been announced.
        if not self.is_leader_ready():
            logger.warning("Leader not marked ready yet")
            return False

        logger.debug("microceph operator is ready for service")
        return True

    def _lookup_system_interfaces(self, mon_hosts: list) -> str:
        """Looks up available addresses on the machine and returns addr if found in mon_hosts."""
        if not mon_hosts:
            return ""

        for intf in netifaces.interfaces():
            addrs = netifaces.ifaddresses(intf)

            # check ipv4 addresses
            for addr in addrs.get(netifaces.AF_INET, []):
                if addr["addr"] in mon_hosts:
                    return addr["addr"]

            # check ipv6 addresses.
            for addr in addrs.get(netifaces.AF_INET6, []):
                if addr["addr"] in mon_hosts:
                    return addr["addr"]

        # return empty string if none found.
        return ""

    def get_ceph_info_from_configs(self, service_name, caps=None) -> dict:
        """Update ceph info from configuration."""
        # public address should be updated once config public-network is supported.
        # get_mon_addresses() cross-checks the live monmap so the published list
        # never advertises a dead mon.
        public_addrs = utils.get_mon_addresses()
        logger.debug(
            "get_ceph_info_from_configs(%s): mon addresses = %s",
            service_name,
            public_addrs,
        )
        return {
            "auth": "cephx",
            "ceph-public-address": self._lookup_system_interfaces(public_addrs),
            "ceph-mon-public-addresses": public_addrs,
            "key": ceph.get_named_key(name=service_name, caps=caps),
        }

    def upgrade_dispatch(self, event: ops.framework.EventBase) -> None:
        """Dispatch upgrade events."""
        logger.debug(f"Dispatch upgrade: {self.unit.name}, {event}")
        dispatch = {
            UpgradeNodeRequestEvent: self.cluster_upgrades.upgrade_node_request,
            UpgradeNodeDoneEvent: self.cluster_upgrades.upgrade_node_done,
        }
        hdlr = dispatch.get(type(event))
        if not hdlr:
            logger.debug(f"Unhandled event: {event}")
            return
        with sunbeam_guard.guard(self, "Upgrading"):
            hdlr(event)

    def configure_app_leader(self, event: ops.framework.EventBase) -> None:
        """Configure the leader unit."""
        logger.debug(f"Configure leader for {event.__repr__}")
        if not self.is_leader_ready():
            if self.model.config.get("wait-to-adopt"):
                logger.info("skipping configure until microceph adopts a remote ceph cluster")
                return

            logger.debug("Bootstrapping MicroCeph cluster")
            self.bootstrap_cluster(event)
            # Record in the same hook that bootstrapped, so the baseline is
            # the option value bootstrap consumed ("" when it fell back to
            # the space subnet), not whatever the option reads as by the
            # time a later hook happens to run.
            self._record_applied_public_network()

        # Handle post bootstrap
        self._handle_post_bootstrap_config(event)

    def _handle_post_bootstrap_config(self, event: ops.framework.EventBase) -> None:
        """Run the post-bootstrap config handlers shared by all bootstrap paths.

        Both configure_app_leader and handle_ceph_adopt must run these; keeping
        one list stops the two paths drifting apart.
        """
        self.handle_config_leader_set_ready()
        self.handle_config_leader_cluster_network(event)
        self.handle_config_leader_ceph_pool_pgs(event)
        self.handle_config_rgw_service(event)
        self.handle_config_leader_charm_upgrade()
        self.handle_config_leader_new_node(event)

    def configure_app_non_leader(self, event: ops.framework.EventBase) -> None:
        """Configure the non leader unit."""
        super().configure_app_non_leader(event)

        logger.debug(f"Configure non leader for {event.__repr__}")
        # MicroClusterNodeAddedEvent triggered only when token is present.
        if isinstance(event, MicroClusterNodeAddedEvent):
            self.cluster_nodes.join_node_to_cluster(event)

        if not self.peers.interface.state.joined:
            # deferral not needed as join token is not yet received.
            raise sunbeam_guard.WaitingExceptionError("waiting to join cluster")

        # Gate active status on the local microceph daemon being fully
        # bootstrapped. ``microceph cluster join`` returns as soon as the
        # dqlite membership is recorded, but the Ceph services (mon/mgr/mds)
        # are started asynchronously and the daemon reports "Daemon not yet
        # initialized" until they are up. Without this gate the unit is marked
        # active before it can actually serve requests. The event is deferred
        # so readiness is re-checked on a later hook; ``join_node_to_cluster``
        # short-circuits on retry so the join is never re-issued.
        # https://github.com/canonical/charm-microceph/issues/80
        if not microceph.is_ready():
            logger.info("microceph daemon not ready after join, deferring")
            event.defer()
            raise sunbeam_guard.WaitingExceptionError("waiting for microceph daemon to bootstrap")

        # Proceed with post join activities
        self.handle_config_rgw_service(event)

    def _get_space_subnet(self, space: str):
        """Get the first available subnet in the network space."""
        space_nets = self.model.get_binding(binding_key=space).network.interfaces
        if space_nets:
            return space_nets[0].subnet

    def _get_network_config(self, option: str) -> str:
        """Fetch and validate a network config option, as a comma-delimited string.

        :param option: ceph-public-network or ceph-cluster-network
        :raises BlockedExceptionError: if any subnet in the option is malformed.
            A bad entry blocks rather than being skipped, so a typo can never
            silently bootstrap the cluster onto a subset of the intended networks.
        """
        value = self.model.config.get(option) or ""
        if not value:
            return ""

        try:
            networks = utils.parse_networks(value)
        except ValueError as e:
            raise sunbeam_guard.BlockedExceptionError(f"Invalid config {option}: {e}")

        return ",".join(networks)

    def _get_bootstrap_params(self) -> dict:
        """Fetch bootstrap parameters."""
        micro_ip = cluster_net = public_net = ""
        snap_channel = snap.SnapCache()["microceph"].channel

        if "quincy" in snap_channel:
            # some quincy snap revisions do not support network configuration.
            # Judge the options by their parsed value, like everywhere else,
            # so whitespace-only input still counts as unset.
            if self._get_network_config(PUBLIC_NETWORK_CONFIG) or self._get_network_config(
                CLUSTER_NETWORK_CONFIG
            ):
                # Block rather than silently bootstrap without the requested
                # networks: the recorded baseline would claim they were applied.
                raise sunbeam_guard.BlockedExceptionError(
                    f"{PUBLIC_NETWORK_CONFIG} and {CLUSTER_NETWORK_CONFIG} are not "
                    "supported on quincy snap channels, unset them"
                )
            logger.warning("Juju spaces incompatible with quincy revision snaps")
            return {}

        availability_zone = os.environ.get("JUJU_AVAILABILITY_ZONE", "")

        # Read config outside the try block: the `finally: return` below would
        # swallow the BlockedExceptionError raised on a malformed subnet.
        public_net_cfg = self._get_network_config(PUBLIC_NETWORK_CONFIG)
        cluster_net_cfg = self._get_network_config(CLUSTER_NETWORK_CONFIG)

        try:
            # Public Network: operator config wins, else the leader's space subnet.
            public_net = public_net_cfg or self._get_space_subnet(space="public") or ""
            # Cluster Network
            cluster_net = cluster_net_cfg or self._get_space_subnet(space="cluster") or ""
            # MicroCeph IP
            micro_ip = self.model.get_binding(binding_key="admin").network.bind_address

            logger.info(
                {
                    "snap-channel": snap_channel,
                    "public_net": public_net,
                    "cluster_net": cluster_net,
                    "micro_ip": micro_ip,
                    "availability_zone": availability_zone,
                }
            )
        except ops.model.ModelError as e:
            logger.exception(e)
        finally:
            return {
                "public_net": format(public_net),
                "cluster_net": format(cluster_net),
                "micro_ip": format(micro_ip),
                "availability_zone": availability_zone,
            }

    def adopt_cluster(self, fsid, mon_hosts, admin_key):
        """Bootstrap Microceph cluster using external ceph cluster."""
        try:
            network_params = self._get_bootstrap_params()
            applied = microceph.adopt_ceph_cluster(
                fsid=fsid,
                mon_hosts=mon_hosts,
                admin_key=admin_key,
                micro_ip=network_params.get("micro_ip", ""),
                public_net=network_params.get("public_net", ""),
                cluster_net=network_params.get("cluster_net", ""),
                availability_zone=network_params.get("availability_zone", ""),
            )
            # mark bootstrap node also as joined
            self.peers.interface.state.joined = True
            if applied.get("availability_zone"):
                self.peers.set_app_data({"cluster_uses_az": "true"})
            # Same-hook baseline recording, as in the bootstrap path.
            self._record_applied_public_network()
            logger.debug("microceph bootstrapped successfully via adopt-ceph")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            if "Unable to initialize cluster: Database is online" in str(e.stderr):
                logger.info("microceph is already bootstrapped, ignore failure.")
                # A retry of the hook that adopted but died before recording:
                # record now, or handle_config_leader_set_ready's upgrade
                # fallback would stamp "" and block the unchanged option.
                self._record_applied_public_network()
                return

            logger.exception("microceph adopt failed:")
            raise

    def bootstrap_cluster(self, event: ops.framework.EventBase) -> None:
        """Bootstrap microceph cluster."""
        try:
            params = self._get_bootstrap_params()
            applied = microceph.bootstrap_cluster(**params)
            logger.debug(f"Successfully bootstrapped with params {params}")
            # mark bootstrap node also as joined
            self.peers.interface.state.joined = True
            if applied.get("availability_zone"):
                self.peers.set_app_data({"cluster_uses_az": "true"})
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(e.stderr)
            error_already_exists = "Unable to initialize cluster: Database is online"
            error_socket_not_exists = "dial unix /var/snap/microceph/common/state/control.socket: connect: no such file or directory"

            if error_socket_not_exists in e.stderr:
                event.defer()
                raise sunbeam_guard.WaitingExceptionError("waiting for snap")

            if error_already_exists not in e.stderr:
                raise e

    def remove_rgw_configs(self, event: ops.framework.EventBase) -> None:
        """Remove RGW configs."""
        if not self.unit.is_leader():
            logger.debug("Not a leader unit, skipping setting rgw config")
            return

        configs = [
            "rgw_keystone_url",
            "rgw_keystone_admin_user",
            "rgw_keystone_admin_password",
            "rgw_keystone_api_version",
            "rgw_keystone_admin_domain",
            "rgw_keystone_admin_project",
            "rgw_keystone_accepted_roles",
            "rgw_keystone_accepted_admin_roles",
            "rgw_keystone_token_cache_size",
            "rgw_keystone_verify_ssl",
            "rgw_keystone_service_token_enabled",
            "rgw_keystone_service_token_accepted_roles",
            "rgw_s3_auth_use_keystone",
            "rgw_swift_account_in_url",
            "rgw_keystone_implicit_tenants",
            "rgw_swift_versioning_enabled",
        ]

        logger.info("Removing RGW Cluster configs")
        microceph.delete_cluster_configs(configs)

    def configure_rgw_service(self, event: ops.framework.EventBase) -> None:
        """Configure RGW service."""
        if not self.unit.is_leader():
            logger.debug("Not a leader unit, skipping setting rgw config")
            return

        configs = {}
        contexts = self.contexts()
        if self.id_svc.ready:
            try:
                configs = {
                    "rgw_keystone_url": self.id_svc.interface.internal_auth_url.removesuffix("v3"),
                    "rgw_keystone_admin_user": self.id_svc.interface.service_user_name,
                    "rgw_keystone_admin_password": self.id_svc.interface.service_password,
                    "rgw_keystone_api_version": "3",
                    "rgw_keystone_admin_domain": self.id_svc.interface.service_domain_name,
                    "rgw_keystone_admin_project": self.id_svc.interface.service_project_name,
                    "rgw_keystone_accepted_roles": "Member,member",
                    "rgw_keystone_accepted_admin_roles": f"{self.id_svc.interface.admin_role},ResellerAdmin",
                    "rgw_keystone_token_cache_size": "500",
                    "rgw_keystone_service_token_enabled": str(True).lower(),
                    "rgw_keystone_service_token_accepted_roles": self.id_svc.interface.admin_role,
                    "rgw_s3_auth_use_keystone": str(True).lower(),
                    "rgw_swift_versioning_enabled": str(True).lower(),
                }

                namespace_projects = self.leader_get("namespace-projects")
                if namespace_projects and json.loads(namespace_projects):
                    configs.update(
                        {
                            "rgw_swift_account_in_url": str(True).lower(),
                            "rgw_keystone_implicit_tenants": str(True).lower(),
                        }
                    )

                if getattr(contexts.receive_ca_cert, "ca_bundle", None):
                    configs["rgw_keystone_verify_ssl"] = str(True).lower()
                else:
                    configs["rgw_keystone_verify_ssl"] = str(False).lower()

                logger.info("Updating RGW Cluster configs")
                microceph.update_cluster_configs(configs)
            except (AttributeError, KeyError) as e:
                logger.warning(f"Not configuring rgw: {str(e)}")
        else:
            self.remove_rgw_configs(event)

    def _update_service_endpoints(self):
        try:
            if self.id_svc.update_service_endpoints:
                logger.debug("Updating service endpoints after ingress relation changed")
                self.id_svc.update_service_endpoints(self.service_endpoints)
        except (AttributeError, KeyError) as e:
            # Ignore AttributeError and KeyError as the exceptions are raised
            # if integration with identity_service is not yet set.
            logger.warning(f"Identity service relation not integrated: {str(e)}")

    def handle_traefik_ready(self, event: ops.framework.EventBase):
        """Handle Traefik route ready callback."""
        if utils.is_departing(self.app):
            logger.debug("Application is being removed; skipping traefik route update")
            return

        if not self.unit.is_leader():
            logger.debug("Not a leader unit, not updating traefik route config")
            return

        if self.traefik_route_rgw and self.traefik_route_rgw.interface.is_ready():
            logger.debug("Sending traefik config for rgw interface")
            self.traefik_route_rgw.interface.submit_to_traefik(config=self.traefik_config)

            if self.traefik_route_rgw.ready:
                if self.model.config.get("enable-rgw") == "*":
                    self.configure_rgw_service(event)
                self._update_service_endpoints()

    def post_config_setup(self):
        """Configuration steps after services have been setup."""
        super().post_config_setup()

    # Callbacks for relation handlers
    def handle_ceph_adopt(self, event) -> None:
        """Callback for interface ceph-admin."""
        # Handle post bootstrap
        logger.debug(f"Handle ceph adopt for {event.__repr__}")
        if self.is_leader_ready():
            logger.debug("Leader already configured, ignoring adopt request")
            return

        # Mark unit as bootstrapped (required for bootstrapped() check)
        self._state.unit_bootstrapped = True
        # The post-bootstrap handlers report retryable conditions by raising
        # waiting/blocked; configure_charm runs them under the base class's
        # guard, so run them under one here too, or a raise would error the
        # hook (and the active statuses below must not paper over it).
        with sunbeam_guard.guard(self, "Adopting existing Ceph cluster"):
            self._handle_post_bootstrap_config(event)
            self.cos_agent._on_refresh(event)
            # Clear any blocked status and set to active (similar to configure_charm)
            self.bootstrap_status.set(ActiveStatus())
            self.status.set(ActiveStatus("charm is ready"))

    def handle_rh_cb_noop(self, event) -> None:
        """Callback for interface ceph."""
        # add a logger debug print for the event name and noop
        logger.debug(f"Callback for {event.relation.name} interface, (noop)")

    # Helpers for charm configuration logic
    def handle_config_leader_set_ready(self):
        """Configure leader as ready."""
        logger.debug("Configuring leader as ready")
        self.set_leader_ready()
        if self.leader_get("namespace-projects") is None:
            self.leader_set(
                {"namespace-projects": json.dumps(self.model.config.get("namespace-projects"))}
            )
        if self.leader_get(PUBLIC_NETWORK_CONFIG) is None:
            # The bootstrap and adopt paths record the value they consumed, so
            # reaching here means the cluster was bootstrapped by a charm
            # revision that predates the option and nothing was ever applied.
            # Record "" (not the current config): a value set only after such
            # an upgrade must block rather than masquerade as applied, since
            # microceph cannot change public_network on a running cluster.
            self.leader_set({PUBLIC_NETWORK_CONFIG: json.dumps("")})

    def _record_applied_public_network(self) -> None:
        """Record the public network consumed at bootstrap, for check_configs.

        First write wins: the baseline must keep describing what bootstrap
        consumed even when this is re-run by a retried hook or a repeated
        event after the option changed. A retry that reaches this only via
        the "already bootstrapped" swallow can still record a value newer
        than the one bootstrap really consumed if the option changed while
        the unit sat in error; the true value is unrecoverable then, and the
        current config is the closest available approximation.

        Recorded as JSON so an unset option ("") stays distinguishable from a
        key that was never written, which peer data cannot express.
        """
        if self.leader_get(PUBLIC_NETWORK_CONFIG) is not None:
            return
        self.leader_set(
            {PUBLIC_NETWORK_CONFIG: json.dumps(self._get_network_config(PUBLIC_NETWORK_CONFIG))}
        )

    def handle_config_leader_cluster_network(self, event: ops.framework.EventBase) -> None:
        """Apply the configured cluster network to the running cluster.

        Unlike public_network, cluster_network can be set on a bootstrapped
        cluster, so it is reconciled on every config-changed rather than only at
        bootstrap. When the value changed, microceph restarts the affected
        services (the momentary service disruption the config option warns
        about).

        The last value this handler applied is tracked in peer data: an
        unchanged option is a no-op without touching the daemon, clearing the
        option after it was set reverts the cluster network to the fallback
        subnet (the leader's subnet in the cluster space), and an option that
        was never set leaves whatever bootstrap applied untouched.

        A pending change that cannot be applied yet defers AND raises waiting:
        the defer gets the event re-delivered, and the raise stops the
        reconcile from ending in active, which would misreport a cluster whose
        requested network is not in effect (the caller's guard turns the raise
        into a waiting status).
        """
        configured = self._get_network_config(CLUSTER_NETWORK_CONFIG)
        recorded = self.leader_get(CLUSTER_NETWORK_CONFIG)
        applied = json.loads(recorded) if recorded else ""

        if configured == applied:
            # Nothing to reconcile. Returning before any daemon call also
            # keeps a steady-state reconcile independent of transient daemon
            # unavailability.
            logger.debug("Cluster network config unchanged, nothing to reconcile")
            return

        cluster_net = configured
        if not configured:
            subnet = self._get_space_subnet(space="cluster")
            if subnet is None:
                # Possibly a transient binding-resolution gap: retry on a
                # later hook rather than silently abandoning the revert.
                event.defer()
                raise sunbeam_guard.WaitingExceptionError(
                    f"No subnet in the cluster space, revert of cluster network {applied} pending"
                )
            cluster_net = str(subnet)
            logger.info(f"Cluster network cleared, reverting to fallback subnet {cluster_net}")

        if not self.ready_for_service():
            event.defer()
            raise sunbeam_guard.WaitingExceptionError(
                f"MicroCeph not ready, update of cluster network to {cluster_net} pending"
            )

        logger.debug(f"Configuring cluster network {cluster_net}")
        try:
            microceph.update_cluster_configs({"cluster_network": cluster_net})
        except ClusterServiceUnavailableException as e:
            logger.warning(str(e))
            event.defer()
            raise sunbeam_guard.WaitingExceptionError(
                f"Cluster service unavailable, update of cluster network to {cluster_net} pending"
            )
        except UnrecognizedClusterConfigOption as e:
            # Not transient: the running microceph rejects the option, so
            # deferring would retry forever. Block until the operator unsets.
            raise sunbeam_guard.BlockedExceptionError(
                f"microceph rejected cluster_network ({e}), unset {CLUSTER_NETWORK_CONFIG}"
            )
        self.leader_set({CLUSTER_NETWORK_CONFIG: json.dumps(configured)})

    def handle_config_leader_ceph_pool_pgs(self, event) -> None:
        """Configure Ceph."""
        logger.debug("Configuring ceph pool replication and pgs")
        if not self.ready_for_service():
            event.defer()
            return

        try:
            default_rf = int(self.model.config.get("default-pool-size"))
        except (TypeError, ValueError) as e:
            logger.error("Invalid value for 'default-pool-size': %s", e)
            raise sunbeam_guard.BlockedExceptionError(
                "Invalid configuration: 'default-pool-size' must be an integer"
            )

        try:
            microceph.set_pool_size("", default_rf)

            # NOTE(utkarshbhatthere): Smaller sunbeam clusters with 3 nodes (and OSDs) and
            # data pools (bulk) for glance, cinder-ceph, gnocchi, rgw, etc will always
            # exhaust the max limit of 250 per OSD. Having a larger limit is preferable
            # over decreasing min_pg for all the pools because the later will reduce distribution
            # in larger clusters.
            ceph.ceph_config_set("global", "mon_max_pg_per_osd", str(MAX_PG_PER_OSD))
        except subprocess.CalledProcessError as e:
            if "unknown command" in e.stderr:
                # Instead of checking for Reef+ in the channel, we run the
                # command and then check against the error message. If the
                # above string is found, then the command isn't present
                # in microceph. Note that we only fail if the replication
                # factor isn't 3, as that is the default, and we run this
                # method on configuration changes.
                if default_rf != 3:
                    event.set_results(
                        {"message": "cannot set pool size: command not supported by microceph"}
                    )
                    event.fail()
                return
            raise e

    def handle_config_rgw_service(self, event: ops.framework.EventBase) -> None:
        """Enable/Disable RGW service."""
        logger.debug("Configuring RGW service")
        try:
            enabled = microceph.is_rgw_enabled(gethostname())
            logger.info(f"Microceph RGW enabled: {enabled}")
            if self.model.config.get("enable-rgw") == "*":
                self.configure_rgw_service(event)
                if not enabled:
                    logger.info("Enabling RGW service")
                    microceph.enable_rgw()
            else:
                if enabled:
                    logger.info("Disabling RGW service")
                    microceph.disable_rgw()
                self.remove_rgw_configs(event)

            # Update traefik lb members
            self.handle_traefik_ready(event)
        except ClusterServiceUnavailableException as e:
            logger.warning(str(e))
            event.defer()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(e.stderr)
            raise e

    def handle_config_leader_charm_upgrade(self) -> None:
        """Handle Charm upgrade events."""
        logger.debug("Handling charm upgrade configuration")
        snap_chan = self.model.config.get("snap-channel")

        if self.cluster_upgrades.upgrade_requested(snap_chan):
            ok, msg = self.cluster_upgrades.can_upgrade_charm_payload(snap_chan)
            if not ok:
                raise sunbeam_guard.BlockedExceptionError(msg)
            self.cluster_upgrades.init_upgrade(snap_chan)

    def handle_config_leader_new_node(self, event: ops.framework.EventBase) -> None:
        """Handle new node configuration."""
        logger.debug("Handling new node configuration")
        if isinstance(event, MicroClusterNewNodeEvent):
            logger.debug(f"Generating join token for {event.unit.name}")
            self.cluster_nodes.add_node_to_cluster(event)


if __name__ == "__main__":  # pragma: no cover
    main(MicroCephCharm)
