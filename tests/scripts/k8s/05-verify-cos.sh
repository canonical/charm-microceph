#!/bin/bash
# ==============================================================================
# 05-verify-cos.sh — Verify Ceph metrics in Prometheus and dashboards in Grafana
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Runs after COS Lite and MicroCeph are deployed and integrated.
# - Requires juju, curl, jq, and python3 on the runner/host.
# - COS_MODEL is the Juju model where COS Lite is deployed.
# - EXPECTED_DASHBOARDS_FILE points to the file listing expected dashboard titles.
# - POLL_ATTEMPTS and POLL_INTERVAL control retry behaviour.
#
# Workflow example:
#   - name: Verify COS integration
#     run: bash tests/scripts/k8s/05-verify-cos.sh
#     env:
#       COS_MODEL: cos-lite
# ==============================================================================

set -euo pipefail

COS_MODEL="${COS_MODEL:-cos-lite}"
EXPECTED_DASHBOARDS_FILE="${EXPECTED_DASHBOARDS_FILE:-$(dirname "$0")/../assets/expected_dashboard.txt}"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-20}"
POLL_INTERVAL_PROM="${POLL_INTERVAL_PROM:-30}"
POLL_INTERVAL_GRAF="${POLL_INTERVAL_GRAF:-60}"

# --- Resolve the expected dashboards file ---
if [ ! -f "${EXPECTED_DASHBOARDS_FILE}" ]; then
    echo "ERROR: Expected dashboards file not found at ${EXPECTED_DASHBOARDS_FILE}" >&2
    exit 1
fi

# --- Switch to COS model ---
echo "==> Switching to Juju model '${COS_MODEL}'"
juju switch "${COS_MODEL}"

# ==============================================================================
# Verify Prometheus metrics
# ==============================================================================
echo "==> Verifying Prometheus metrics"
prom_addr=$(juju status --format json | jq -r '.applications.prometheus.address')

for i in $(seq 1 "${POLL_ATTEMPTS}"); do
    curl_output=$(curl -s "http://${prom_addr}:9090/api/v1/query?query=ceph_health_detail")
    prom_status=$(echo "$curl_output" | jq -r '.status')
    result_count=$(echo "$curl_output" | jq '.data.result | length')
    if [[ "$prom_status" == "success" && "$result_count" -gt 0 ]]; then
        echo "    Ceph metrics found in Prometheus (${result_count} result(s))"
        break
    fi
    echo "    Waiting for ceph metrics in Prometheus (attempt ${i}/${POLL_ATTEMPTS})..."
    sleep "${POLL_INTERVAL_PROM}"
done

# Final assertion
curl_output=$(curl -s "http://${prom_addr}:9090/api/v1/query?query=ceph_health_detail")
prom_status=$(echo "$curl_output" | jq -r '.status')
result_count=$(echo "$curl_output" | jq '.data.result | length')
if [[ "$prom_status" != "success" || "$result_count" -eq 0 ]]; then
    echo "ERROR: Prometheus query for ceph_health_detail failed or returned no results: $curl_output" >&2
    exit 1
fi
echo "==> Prometheus metrics verification passed."

# ==============================================================================
# Verify Grafana dashboards
# ==============================================================================
echo "==> Verifying Grafana dashboards"
graf_addr=$(juju status --format json | jq -r '.applications.grafana.address')

get_admin_action=$(juju run grafana/0 get-admin-password --format json --wait 5m)
action_status=$(echo "$get_admin_action" | jq -r '."grafana/0".status')
if [[ "$action_status" != "completed" ]]; then
    echo "ERROR: Failed to fetch admin password from grafana: $get_admin_action" >&2
    exit 1
fi
grafana_pass=$(echo "$get_admin_action" | jq -r '."grafana/0".results."admin-password"')

expected_dashboard_count=$(wc -l < "${EXPECTED_DASHBOARDS_FILE}")

for i in $(seq 1 "${POLL_ATTEMPTS}"); do
    curl -s "http://admin:${grafana_pass}@${graf_addr}:3000/api/search" \
        | jq '.[].title' | jq -s 'sort' > dashboards.json
    cat dashboards.json
    match_count=$(grep -F -c -f "${EXPECTED_DASHBOARDS_FILE}" dashboards.json || true)
    if [[ "$match_count" -eq "$expected_dashboard_count" ]]; then
        echo "    All expected dashboards present"
        break
    fi
    echo "    Waiting for dashboards (attempt ${i}/${POLL_ATTEMPTS}, ${match_count}/${expected_dashboard_count} matched)..."
    sleep "${POLL_INTERVAL_GRAF}"
done

# Final assertion
match_count=$(grep -F -c -f "${EXPECTED_DASHBOARDS_FILE}" dashboards.json || true)
if [[ "$match_count" -ne "$expected_dashboard_count" ]]; then
    echo "ERROR: Required dashboards still not present (${match_count}/${expected_dashboard_count})." >&2
    cat dashboards.json
    exit 1
fi
echo "==> Grafana dashboards verification passed."

echo ""
echo "==> COS verification complete."
