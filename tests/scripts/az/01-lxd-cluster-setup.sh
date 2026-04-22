#!/bin/bash

# Bootstrap an LXD cluster with NODES nodes and NODES failure domains.

set -eux

NODES=${NODES:-3}
ROOT_SZ=${ROOT_SZ:-20GiB}
MEM_SZ=${MEM_SZ:-8GiB}
CORES=${CORES:-4}
HOST_BRIDGE=${HOST_BRIDGE:-jujubr}

wait_for_agent() {
    local node="$1"
    echo "Waiting for LXD guest agent on ${node}..."
    until lxc exec "${node}" -- true 2>/dev/null; do
        sleep 2
    done
}

wait_for_snapd() {
    local node="$1"
    echo "Waiting for snapd seed on ${node}..."
    lxc exec "${node}" -- snap wait system seed.loaded
}

get_iface_ip() {
    local node="$1"
    local iface="$2"
    lxc exec "${node}" -- ip -4 -o addr show dev "${iface}" | awk '{print $4}' | cut -d/ -f1 | head -1
}

wait_for_iface_ip() {
    local node="$1"
    local iface="$2"
    local ip

    for _ in $(seq 1 30); do
        ip=$(get_iface_ip "${node}" "${iface}" || true)
        if [ -n "${ip}" ]; then
            echo "${ip}"
            return 0
        fi
        sleep 1
    done

    echo "ERROR: ${node}:${iface} did not get an IPv4 address in time." >&2
    return 1
}

configure_underlay_bridge() {
    local node="$1"
    local bridge="$2"

    # This block runs inside each node VM.
    # It creates an explicit host bridge and migrates default-route NIC into it.
    lxc exec "${node}" -- bash -s -- "${bridge}" <<'EOS'
set -eux
bridge="$1"

if ip link show "${bridge}" >/dev/null 2>&1; then
  echo "Bridge ${bridge} already exists; skipping."
  exit 0
fi

default_iface="$(ip route show default | awk '/default/ {print $5; exit}')"
if [ -z "${default_iface}" ]; then
  echo "ERROR: Could not detect default route interface." >&2
  exit 1
fi

mkdir -p /root/netplan-backup
cp -a /etc/netplan/*.yaml /root/netplan-backup/ 2>/dev/null || true

# Bridge plan:
# - disable DHCP on raw NIC
# - enable DHCP on bridge that owns that NIC
# This moves the node's L3 identity to the bridge and allows LXD NIC parenting.
cat >/etc/netplan/99-bridge-${bridge}.yaml <<EOF
network:
  version: 2
  ethernets:
    ${default_iface}:
      dhcp4: false
      dhcp6: false
  bridges:
    ${bridge}:
      interfaces:
        - ${default_iface}
      dhcp4: true
      dhcp6: false
      parameters:
        stp: false
        forward-delay: 0
EOF

netplan apply

for _ in $(seq 1 20); do
  if ip -4 -o addr show dev "${bridge}" | grep -q 'inet '; then
    ip -4 addr show dev "${bridge}"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Bridge ${bridge} did not get IPv4 connectivity." >&2
exit 1
EOS
}

cluster_bootstrap() {
    local node="$1"
    local ip="$2"
    local bridge="$3"

    # Pattern aligned with:
    # https://github.com/canonical/sqa-cloud-deployment-pipeline/blob/main/products/sunbeam/external_juju_controller/preseed_template.yaml
    # profile default -> nictype=bridged parent=<explicit host bridge>
    # plus explicit core.https_address.
    lxc exec "${node}" -- lxd init --preseed <<EOF
config:
  core.https_address: ${ip}:8443
networks: []
storage_pools:
- config: {}
  description: ""
  name: local
  driver: dir
storage_volumes: []
profiles:
- config: {}
  description: ""
  devices:
    eth0:
      name: eth0
      nictype: bridged
      parent: ${bridge}
      type: nic
    root:
      path: /
      pool: local
      type: disk
  name: default
projects: []
cluster:
  server_name: ${node}
  enabled: true
  server_address: ${ip}:8443
  member_config: []
  cluster_address: ""
  cluster_certificate: ""
  cluster_token: ""
  cluster_certificate_path: ""
EOF
}

cluster_join() {
    local node="$1"
    local ip="$2"
    local cluster_ip="$3"
    local token="$4"

    # Pattern aligned with:
    # https://github.com/canonical/sqa-cloud-deployment-pipeline/blob/main/products/sunbeam/external_juju_controller/preseed_member_template.yaml
    # explicit server_address + cluster_address + token for deterministic join.
    lxc exec "${node}" -- lxd init --preseed <<EOF
cluster:
  server_name: ${node}
  enabled: true
  server_address: ${ip}:8443
  cluster_address: ${cluster_ip}:8443
  cluster_token: ${token}
  member_config:
  - entity: storage-pool
    name: local
    key: source
    value: ""
EOF
}

set_failure_domain() {
    local bootstrap_node="$1"
    local node="$2"
    local zone="$3"
    lxc exec "${bootstrap_node}" -- lxc cluster failure-domain set "${node}" "${zone}"
}

declare -a node_ips

for ((i=1; i<=NODES; i++)); do
    lxc launch ubuntu:24.04 "node${i}" --vm --device root,size="${ROOT_SZ}" --config limits.memory="${MEM_SZ}" --config limits.cpu="${CORES}"
    wait_for_agent "node${i}"
    wait_for_snapd "node${i}"
    lxc exec "node${i}" -- snap install lxd --channel 6/stable

    configure_underlay_bridge "node${i}" "${HOST_BRIDGE}"
    node_ips[$i]=$(wait_for_iface_ip "node${i}" "${HOST_BRIDGE}")
done

bootstrap_ip="${node_ips[1]}"
cluster_bootstrap "node1" "${bootstrap_ip}" "${HOST_BRIDGE}"
set_failure_domain "node1" "node1" "zone1"

for ((i=2; i<=NODES; i++)); do
    token=$(lxc exec node1 -- lxc cluster add "node${i}" --quiet)
    node_ip="${node_ips[$i]}"
    cluster_join "node${i}" "${node_ip}" "${bootstrap_ip}" "${token}"
    set_failure_domain "node1" "node${i}" "zone${i}"

done

echo "LXD cluster setup complete with explicit bridge '${HOST_BRIDGE}' on all members."
