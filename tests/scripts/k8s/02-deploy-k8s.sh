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
# Every k8s CLI verb that waits (bootstrap, status --wait-ready, enable, set)
# defaults to --timeout 90s. Cluster readiness alone takes 47-87s on the CI
# runners, so the default trips on a slow runner. Give it real headroom.
K8S_CMD_TIMEOUT="${K8S_CMD_TIMEOUT:-10m}"
# Seconds to wait for MetalLB's controller/speaker rollouts.
METALLB_TIMEOUT="${METALLB_TIMEOUT:-600}"

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
lxc exec "${VM_NAME}" -- k8s bootstrap --timeout "${K8S_CMD_TIMEOUT}"

echo "==> Waiting for cluster to be ready"
lxc exec "${VM_NAME}" -- k8s status --wait-ready --timeout "${K8S_CMD_TIMEOUT}"

# --- Enable ingress and load-balancer ---
echo "==> Enabling ingress"
lxc exec "${VM_NAME}" -- k8s enable ingress --timeout "${K8S_CMD_TIMEOUT}"

echo "==> Enabling load-balancer"
lxc exec "${VM_NAME}" -- k8s enable load-balancer --timeout "${K8S_CMD_TIMEOUT}"

# `k8s enable load-balancer` returns before MetalLB's workloads have been
# created, and `kubectl wait` exits immediately with "no matching resources
# found" when its selector matches nothing. So: poll until the controller
# Deployment and the speaker DaemonSet exist, then wait for their rollouts.
echo "==> Waiting for MetalLB workloads to roll out (timeout: ${METALLB_TIMEOUT}s)"
metallb_kubectl() {
  lxc exec "${VM_NAME}" -- k8s kubectl -n metallb-system "$@"
}
deadline=$(( $(date +%s) + METALLB_TIMEOUT ))
workloads=""
while :; do
  workloads=$(metallb_kubectl get deployment,daemonset \
    -l app.kubernetes.io/name=metallb -o name 2>/dev/null || true)
  if echo "${workloads}" | grep -q '^deployment' && echo "${workloads}" | grep -q '^daemonset'; then
    break
  fi
  if (( $(date +%s) >= deadline )); then
    echo "ERROR: MetalLB workloads did not appear within ${METALLB_TIMEOUT}s" >&2
    metallb_kubectl get all 2>/dev/null || true
    exit 1
  fi
  echo "    Waiting for MetalLB workloads to be created..."
  sleep 5
done
for workload in ${workloads}; do
  remaining=$(( deadline - $(date +%s) ))
  if (( remaining <= 0 )); then
    echo "ERROR: ran out of time waiting for MetalLB rollouts" >&2
    exit 1
  fi
  metallb_kubectl rollout status "${workload}" --timeout="${remaining}s"
done

# The load-balancer config is validated by MetalLB's admission webhook, which
# can lag its pods by a few seconds; retry rather than fail on the first try.
echo "==> Configuring load-balancer L2 mode with CIDRs: ${LB_CIDRS}"
for i in $(seq 1 12); do
  if lxc exec "${VM_NAME}" -- k8s set --timeout "${K8S_CMD_TIMEOUT}" \
      load-balancer.cidrs="${LB_CIDRS}" \
      load-balancer.l2-mode=true; then
    break
  fi
  if [ "${i}" -eq 12 ]; then
    echo "ERROR: could not apply load-balancer configuration" >&2
    exit 1
  fi
  echo "    load-balancer config not accepted yet (attempt ${i}/12), retrying in 10s..."
  sleep 10
done

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
