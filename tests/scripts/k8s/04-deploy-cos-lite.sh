#!/bin/bash
# ==============================================================================
# 04-deploy-cos-lite.sh — Deploy COS Lite bundle, grant trust, wait for active
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Runs after 03-juju-add-cloud.sh; Juju controller must be bootstrapped.
# - COS Lite charms require cluster-scoped RBAC (--trust). The bundle deploy
#   does NOT propagate --trust to individual charms, so we grant trust to each
#   application explicitly after deploy. This prevents the "Unauthorized" errors
#   seen with prometheus-k8s and transient failures in grafana/loki.
# - The wait loop at the end polls `juju status` until all units reach
#   active/idle or until WAIT_TIMEOUT is exceeded. Adjust WAIT_TIMEOUT for CI.
# - MODEL_NAME can be customized; defaults to "cos-lite".
#
# Workflow example:
#   - name: Deploy COS Lite
#     run: bash 04-deploy-cos-lite.sh
#     timeout-minutes: 30
#     env:
#       MODEL_NAME: cos-lite
#       WAIT_TIMEOUT: 1200
# ==============================================================================

set -euo pipefail

MODEL_NAME="${MODEL_NAME:-cos-lite}"
# Maximum seconds to wait for all units to settle to active/idle.
WAIT_TIMEOUT="${WAIT_TIMEOUT:-1200}"

# --- Create the model ---
echo "==> Adding model '${MODEL_NAME}'"
juju add-model "${MODEL_NAME}"

# --- Deploy the COS Lite bundle ---
echo "==> Deploying cos-lite bundle"
juju deploy cos-lite --trust

# --- Grant cluster trust to every COS Lite application ---
# The --trust flag on bundle deploy does not always propagate correctly.
# Granting explicitly avoids "Unauthorized" errors from prometheus-k8s
# (resource limit patch), grafana-k8s (dashboard relations), and loki-k8s
# (agent connectivity issues).
echo "==> Granting cluster-scoped trust to all COS Lite applications"
COS_APPS="prometheus alertmanager grafana loki traefik catalogue"
for app in ${COS_APPS}; do
    echo "    Trusting ${app}"
    juju trust "${app}" --scope=cluster
done

# --- Wait for all units to reach active/idle ---
echo "==> Waiting for all units to settle (timeout: ${WAIT_TIMEOUT}s)"
SECONDS=0
while true; do
    # Count units that are NOT active/idle
    # juju status --format short gives lines like: - app/0: (agent:idle, workload:active)
    NOT_READY=$(juju status --format json | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
apps = data.get('applications', {})
count = 0
for aname, ainfo in apps.items():
    units = ainfo.get('units', {})
    for uname, uinfo in units.items():
        ws = uinfo.get('workload-status', {}).get('current', '')
        agent_status = uinfo.get('juju-status', {}).get('current', '')
        if ws != 'active' or agent_status != 'idle':
            count += 1
print(count)
" 2>/dev/null || echo "999")

    if [ "${NOT_READY}" -eq 0 ]; then
        echo "==> All units are active/idle."
        break
    fi

    if [ "${SECONDS}" -ge "${WAIT_TIMEOUT}" ]; then
        echo "ERROR: Timed out waiting for units. ${NOT_READY} unit(s) not ready." >&2
        juju status
        exit 1
    fi

    echo "    ${NOT_READY} unit(s) not ready yet (${SECONDS}s elapsed)..."
    sleep 15
done

echo ""
echo "==> COS Lite deployment complete."
juju status
