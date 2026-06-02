#!/bin/bash
# ==============================================================================
# 02-deploy-k8s.sh — Install Canonical k8s snap, bootstrap, enable features
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Runs after 01-setup-lxd-vm.sh; depends on VM_NAME being set.
# - K8S_CHANNEL controls which snap channel to install from. Pin this in CI
#   to avoid surprises from channel updates.
# - The load-balancer CIDR range (LB_CIDRS) MUST be on the same subnet as the
#   LXD bridge so that LB VIPs are reachable from the host/runner. The default
#   range uses .220-.240 of the bridge subnet — adjust if your bridge uses a
#   different range or if those IPs conflict.
# - Outputs a kubeconfig file to KUBECONFIG_PATH for subsequent scripts.
#
# Workflow example:
#   - name: Deploy k8s
#     run: bash 02-deploy-k8s.sh
#     env:
#       VM_NAME: k8s-node
#       LB_CIDRS: "10.105.154.220-10.105.154.240"
# ==============================================================================

set -euo pipefail

# --- Configurable variables ---
VM_NAME="${VM_NAME:-k8s-node}"
K8S_CHANNEL="${K8S_CHANNEL:-1.32-classic/stable}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-$(pwd)/kubeconfig.yaml}"

# Load-balancer IP range — must be routable from the host via the LXD bridge.
# Auto-detect from the bridge if not explicitly set.
echo "==> Detecting VM IP address..."
VM_IP=""
for i in $(seq 1 30); do
  VM_IP=$(lxc list "${VM_NAME}" --format csv -c 4 | grep -oP '^\d+\.\d+\.\d+\.\d+' | head -1)
  if [ -n "${VM_IP}" ]; then
    echo "    VM IP: ${VM_IP}"
    break
  fi
  echo "    Waiting for IPv4 address (attempt ${i}/30)..."
  sleep 2
done

if [ -z "${LB_CIDRS:-}" ]; then
  BRIDGE_SUBNET=$(echo "${VM_IP}" | grep -oP '^\d+\.\d+\.\d+')
  if [ -n "${BRIDGE_SUBNET}" ]; then
    LB_CIDRS="${BRIDGE_SUBNET}.220-${BRIDGE_SUBNET}.240"
  else
    echo "ERROR: Could not detect IPv4 address for VM '${VM_NAME}'." >&2
    echo "Note: This script requires IPv4 for k8s load-balancer CIDRs." >&2
    exit 1
  fi
fi

# --- Install k8s snap ---
echo "==> Installing k8s snap (channel: ${K8S_CHANNEL})"
lxc exec "${VM_NAME}" -- snap install k8s --classic --channel="${K8S_CHANNEL}"

# --- Bootstrap the cluster ---
echo "==> Bootstrapping k8s cluster"
lxc exec "${VM_NAME}" -- k8s bootstrap

echo "==> Waiting for cluster to be ready"
lxc exec "${VM_NAME}" -- k8s status --wait-ready

# --- Enable ingress and load-balancer ---
echo "==> Enabling ingress"
lxc exec "${VM_NAME}" -- k8s enable ingress

echo "==> Enabling load-balancer"
lxc exec "${VM_NAME}" -- k8s enable load-balancer

# MetalLB webhook needs its pods running before config can be applied.
# `kubectl wait --for=condition=ready pod` exits immediately with an error when
# zero pods match the selector (upstream kubectl bug, still open in 1.32:
# https://github.com/kubernetes/kubectl/issues/1675). `k8s enable load-balancer`
# returns before MetalLB's Deployment has been scheduled, so we must poll until
# at least one Deployment exists before calling `kubectl wait`.
echo "==> Waiting for MetalLB deployments to appear"
for i in $(seq 1 60); do
  count=$(lxc exec "${VM_NAME}" -- k8s kubectl get deployment \
    -l app.kubernetes.io/name=metallb \
    -n metallb-system \
    --no-headers 2>/dev/null | wc -l)
  if [ "${count}" -gt 0 ]; then
    echo "    MetalLB deployments found (${count})"
    break
  fi
  echo "    Waiting for MetalLB deployments to be scheduled (attempt ${i}/60)..."
  sleep 3
done

echo "==> Waiting for MetalLB deployments to become available"
lxc exec "${VM_NAME}" -- k8s kubectl wait \
  --for=condition=available deployment \
  -l app.kubernetes.io/name=metallb \
  -n metallb-system \
  --timeout=180s

echo "==> Waiting for MetalLB pods to be ready"
lxc exec "${VM_NAME}" -- k8s kubectl wait \
  --for=condition=ready pod \
  -l app.kubernetes.io/name=metallb \
  -n metallb-system \
  --timeout=180s

echo "==> Configuring load-balancer L2 mode with CIDRs: ${LB_CIDRS}"
lxc exec "${VM_NAME}" -- k8s set \
  load-balancer.cidrs="${LB_CIDRS}" \
  load-balancer.l2-mode=true

# --- Export kubeconfig ---
echo "==> Exporting kubeconfig to ${KUBECONFIG_PATH}"
lxc exec "${VM_NAME}" -- k8s config >"${KUBECONFIG_PATH}"

# --- Final status ---
echo "==> Cluster status:"
lxc exec "${VM_NAME}" -- k8s status
echo ""
echo "==> Nodes:"
lxc exec "${VM_NAME}" -- k8s kubectl get nodes -o wide
echo ""
echo "KUBECONFIG=${KUBECONFIG_PATH}"
