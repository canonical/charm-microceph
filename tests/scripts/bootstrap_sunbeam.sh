#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a single-node Sunbeam deployment suitable for the attached-model
# MicroCeph end-to-end suite.

SUNBEAM_MODEL="${SUNBEAM_MODEL:-sunbeam-controller:admin/openstack-machines}"
JUJU_CHANNEL="${JUJU_CHANNEL:-3.6/stable}"
OPENSTACK_CHANNEL="${OPENSTACK_CHANNEL:-2024.1}"

ensure_nft_forward_policy_accept() {
    local family="$1"

    sudo nft list table "$family" filter >/dev/null 2>&1 || sudo nft add table "$family" filter

    if sudo nft list chain "$family" filter FORWARD >/dev/null 2>&1; then
        sudo nft flush chain "$family" filter FORWARD || true
        sudo nft chain "$family" filter FORWARD '{ policy accept; }'
        return
    fi

    sudo nft add chain "$family" filter FORWARD '{ type filter hook forward priority 0; policy accept; }'
}

ensure_xtables_forward_policy_accept() {
    local tool="$1"

    if ! command -v "$tool" >/dev/null 2>&1; then
        return
    fi

    sudo "$tool" -P FORWARD ACCEPT || true
    sudo "$tool" -F FORWARD || true
}

ensure_forward_policy_accept() {
    ensure_nft_forward_policy_accept ip
    ensure_nft_forward_policy_accept ip6
    ensure_xtables_forward_policy_accept iptables
    ensure_xtables_forward_policy_accept ip6tables
}

ensure_snap() {
    local snap_name="$1"
    local channel="$2"

    if sudo snap list "$snap_name" >/dev/null 2>&1; then
        sudo snap refresh "$snap_name" --channel "$channel"
    else
        sudo snap install "$snap_name" --channel "$channel"
    fi
}

echo "==> Preparing host for Sunbeam bootstrap"
date
sudo snap remove --purge lxd || true
sudo apt-get remove --purge -y docker.io containerd runc || true
sudo rm -rf /run/containerd
ensure_forward_policy_accept

echo "==> Installing Juju and Sunbeam snap dependencies"
ensure_snap juju "$JUJU_CHANNEL"
ensure_snap openstack "$OPENSTACK_CHANNEL"
sudo snap connect openstack:juju-bin juju:juju-bin || true

echo "==> Bootstrapping Sunbeam"
sunbeam prepare-node-script --bootstrap | bash -x
sg snap_daemon "sunbeam cluster bootstrap --accept-defaults --topology single --database single"
sg snap_daemon "sunbeam cluster list"
juju status -m admin/controller
juju status -m openstack
sg snap_daemon "sunbeam configure --accept-defaults --openrc demo-openrc"
juju status -m "$SUNBEAM_MODEL"
date
