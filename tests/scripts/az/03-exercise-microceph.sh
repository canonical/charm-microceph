#!/bin/bash
# Functions for deploying, exercising, and verifying the microceph charm on the
# LXD cluster set up by 01-lxd-cluster-setup.sh / 02-juju-bootstrap.sh.
#
# Usage (direct invocation):
#   ./03-exercise-microceph.sh <function> [args...]
#
# Usage (sourcing):
#   source ./03-exercise-microceph.sh
#   deploy_from_charm_file
#   add_osd_loop
#   verify_az_crush_map

set -eux

NODES=${NODES:-3}
CONTROLLER=${CONTROLLER:-lxd-cluster}
MODEL=${MODEL:-microceph}
BASE_CHANNEL=${BASE_CHANNEL:-ubuntu@24.04}
CHARM_PATH=${CHARM_PATH:-./microceph_amd64.charm}
CHARM_REVISION=${CHARM_REVISION:-227}
CHARM_TRACK=${CHARM_TRACK:-squid/stable}
SNAP_CHANNEL=${SNAP_CHANNEL:-squid/stable}

# Constraints applied to each microceph unit container.
UNIT_CONSTRAINTS="virt-type=container root-disk-source=local"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_juju() {
    lxc exec node1 -- juju "$@"
}

_juju_ssh() {
    lxc exec node1 -- juju ssh --pty=false "$@"
}

# The juju snap uses strict confinement and cannot access the system /tmp.
# /var/snap/juju/common/ is always accessible to the snap regardless of user.
_push_charm() {
    lxc file push "${CHARM_PATH}" node1/var/snap/juju/common/charm.charm
}

_wait_for_osds() {
    local expect="${1?missing}"
    local up_count=0

    for _ in $(seq 1 30); do
        up_count=$(_juju_ssh microceph/0 -- \
            sudo microceph.ceph osd stat --format json 2>/dev/null | \
            jq -r '.num_up_osds // 0' 2>/dev/null || echo 0)
        if [ "${up_count:-0}" -ge "${expect}" ]; then
            echo "Found ${up_count} >= ${expect} OSDs up"
            return 0
        fi
        echo "  ${up_count:-0}/${expect} OSDs up, waiting..."
        sleep 10
    done
    echo "ERROR: Only ${up_count:-0}/${expect} OSDs came up after 300s"
    return 1
}

_ensure_model() {
    _juju add-model "${MODEL}" --controller "${CONTROLLER}" || true
    _juju switch "${MODEL}"
}

# ---------------------------------------------------------------------------
# Deployment functions
# ---------------------------------------------------------------------------

deploy_from_charm_file() {
    # Deploy from a local .charm file (CHARM_PATH).
    _push_charm
    _ensure_model

    _juju deploy /var/snap/juju/common/charm.charm microceph \
        --to "node1" \
        --constraints "${UNIT_CONSTRAINTS}" \
        --config "snap-channel=${SNAP_CHANNEL}" \
        --base "${BASE_CHANNEL}"

    _juju wait-for application microceph --query='status=="active"' --timeout 30m

    for ((i=2; i<=NODES; i++)); do
        _juju add-unit microceph --to "node${i}"
    done

    _juju wait-for application microceph --query='status=="active"' --timeout 30m
}

deploy_from_revision() {
    # Deploy from Charmhub at a specific revision and track
    _ensure_model

    _juju deploy ch:microceph \
        --to "node1" \
        --constraints "${UNIT_CONSTRAINTS}" \
        --revision "${CHARM_REVISION}" \
        --channel "${CHARM_TRACK}" \
        --config "snap-channel=${SNAP_CHANNEL}" \
        --base "${BASE_CHANNEL}"

    _juju wait-for application microceph --query='status=="active"' --timeout 30m

    for ((i=2; i<=NODES; i++)); do
        _juju add-unit microceph --to "node${i}"
    done

    _juju wait-for application microceph --query='status=="active"' --timeout 30m
}

_wait_for_az_snap() {
    for _ in $(seq 1 30); do
        local ready=0
        for ((i=0; i<NODES; i++)); do
            if _juju exec --unit "microceph/${i}" 'microceph cluster bootstrap --help' 2>/dev/null \
                    | grep -q -- '--availability-zone'; then
                ready=$((ready + 1))
            fi
        done
        if [ "${ready}" -eq "${NODES}" ]; then
            echo "Upgrade complete: all ${NODES} units support --availability-zone"
            return 0
        fi
        echo "  ${ready}/${NODES} units upgraded, waiting..."
        sleep 10
    done
    echo "ERROR: only ${ready}/${NODES} units gained --availability-zone support after 300s"
    _juju status
    return 1
}

upgrade_to_charm_file() {
    # Refresh an existing microceph deployment to the local charm file.
    _push_charm
    _juju refresh microceph --path=/var/snap/juju/common/charm.charm \
        --config "snap-channel=${SNAP_CHANNEL}"
    _wait_for_az_snap
}

add_osd_loop() {
    # Add a 1 GiB loop-device OSD to each unit.
    for ((i=0; i<NODES; i++)); do
        _juju run "microceph/${i}" add-osd loop-spec="1G,1"
    done
    _wait_for_osds "${NODES}"
}

join_additional_unit() {
    # Add a new microceph unit to an existing LXD cluster node, add a loop OSD,
    # and verify the unit joins the MicroCeph cluster with the cluster remaining healthy.
    local node="${1:-node3}"

    local osd_count_before
    osd_count_before=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph osd stat --format json 2>/dev/null | jq -r '.num_osds // 0')

    echo "=== join_additional_unit: adding unit to ${node} (current OSDs: ${osd_count_before}) ==="
    _juju add-unit microceph --to "${node}"
    _juju wait-for application microceph --query='status=="active"' --timeout 30m

    # New unit index: units 0..NODES-1 already exist, so the new unit is NODES.
    local new_unit="microceph/${NODES}"
    _juju run "${new_unit}" add-osd loop-spec="1G,1"
    _wait_for_osds $((osd_count_before + 1))

    _juju_ssh microceph/0 -- sudo microceph.ceph -s
    _juju_ssh microceph/0 -- sudo microceph.ceph osd tree

    local health
    health=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph health --format json 2>/dev/null | jq -r '.status // "UNKNOWN"')
    if [ "${health}" = "HEALTH_ERR" ]; then
        echo "FAIL: Cluster health is ${health} after adding new unit"
        _juju_ssh microceph/0 -- sudo microceph.ceph health detail
        return 1
    fi
    echo "PASSED: Additional unit joined and OSD became active (health=${health})"
}

prune_units() {
    # destroy-model removes all applications, machines, and storage and waits
    _juju destroy-model "${MODEL}" --no-prompt --destroy-storage --no-wait --force || true
}

collect_logs() {
    mkdir -p logs
    _juju status | tee logs/juju-status.txt || true
    _juju debug-log --replay --no-tail | tee logs/juju-debug-log.txt || true
}

relax_clock_skew() {
    # GitHub Actions runners block outbound UDP 123 so VMs can't sync NTP.
    # Raise mon_clock_drift_allowed to 2s to prevent HEALTH_WARN from blocking upgrades.
    _juju exec --unit microceph/leader -- sudo microceph.ceph config set mon mon_clock_drift_allowed 2
    _juju_ssh microceph/0 -- sudo microceph.ceph health detail
}

# ---------------------------------------------------------------------------
# Verification functions
# ---------------------------------------------------------------------------

verify_az_crush_map() {
    # Verify that after deploying with AZ-aware charm + OSDs across all zones:
    #   1. The default CRUSH rule is the rack rule (microceph_auto_rack).
    #   2. Each zone has a corresponding rack bucket (az.nodeN) in the tree.

    echo "=== verify_az_crush_map ==="
    _juju_ssh microceph/0 -- sudo microceph.ceph osd tree

    local default_rule
    default_rule=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph config get mon osd_pool_default_crush_rule | tr -d '[:space:]')

    local rack_rule_id
    rack_rule_id=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph osd crush rule dump microceph_auto_rack --format json | \
        jq -r '.rule_id')

    if [ "${default_rule}" != "${rack_rule_id}" ]; then
        echo "FAIL: default crush rule (${default_rule}) is not the rack rule (${rack_rule_id})"
        return 1
    fi
    echo "Default CRUSH rule is rack (id=${rack_rule_id}): OK"

    for ((i=1; i<=NODES; i++)); do
        echo "Checking for az.node${i} in CRUSH tree..."
        _juju_ssh microceph/0 -- sudo microceph.ceph osd tree | grep -F "az.node${i}"
    done

    echo "PASSED: AZ CRUSH map verification"
}

verify_no_az_on_upgrade() {
    # Verify that upgrading a cluster bootstrapped without AZ support does NOT
    # retroactively activate AZ CRUSH rules.
    #
    # Pre-conditions: upgrade_to_charm_file has been called and the cluster was
    # originally deployed via deploy_from_revision using a pre-AZ charm revision.

    echo "=== verify_no_az_on_upgrade ==="
    _juju_ssh microceph/0 -- sudo microceph.ceph osd tree
    _juju_ssh microceph/0 -- sudo microceph.ceph -s

    echo "--- CRUSH rules ---"
    _juju_ssh microceph/0 -- sudo microceph.ceph osd crush rule dump --format json | \
        jq -r '.[] | "  id=\(.rule_id) name=\(.rule_name)"'

    local default_rule
    default_rule=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph config get mon osd_pool_default_crush_rule | tr -d '[:space:]')

    local default_rule_name
    default_rule_name=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph osd crush rule dump --format json | \
        jq -r --argjson id "${default_rule}" '.[] | select(.rule_id == $id) | .rule_name')

    echo "Default CRUSH rule: id=${default_rule} name=${default_rule_name}"

    local rack_rule_id
    rack_rule_id=$(_juju_ssh microceph/0 -- \
        sudo microceph.ceph osd crush rule dump microceph_auto_rack --format json 2>/dev/null | \
        jq -r '.rule_id // empty')

    if [ "${default_rule}" = "${rack_rule_id}" ]; then
        echo "FAIL: after upgrade, default crush rule (id=${default_rule} name=${default_rule_name}) is the rack rule; AZ CRUSH rules must not be retroactively applied"
        return 1
    fi
    echo "Default CRUSH rule (id=${default_rule} name=${default_rule_name}) is not the rack rule after upgrade: OK"

    for ((i=1; i<=NODES; i++)); do
        if _juju_ssh microceph/0 -- sudo microceph.ceph osd tree | grep -qF "az.node${i}"; then
            echo "FAIL: az.node${i} found in CRUSH tree after upgrade from non-AZ revision"
            return 1
        fi
        echo "az.node${i} absent from CRUSH tree: OK"
    done

    echo "PASSED: No AZ CRUSH activation after upgrade from pre-AZ revision"
}

# ---------------------------------------------------------------------------
# Entry point — allow calling individual functions by name
# ---------------------------------------------------------------------------

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    cmd="${1:-}"
    if [ -n "${cmd}" ]; then
        shift
        "${cmd}" "$@"
    else
        echo "Usage: $0 <function> [args...]"
        echo ""
        echo "Deployment:"
        echo "  deploy_from_charm_file   Deploy from local CHARM_PATH (default: ./microceph_amd64.charm)"
        echo "  deploy_from_revision     Deploy from Charmhub CHARM_TRACK rev CHARM_REVISION (default: squid/stable rev 227)"
        echo "  upgrade_to_charm_file    Refresh existing deployment to local CHARM_PATH"
        echo "  add_osd_loop             Add a 1G loop OSD to each unit"
        echo "  join_additional_unit     Add a unit to a node (default: node3), add OSD, verify health"
        echo "  prune_units              Remove all units and destroy the model"
        echo "  relax_clock_skew         Set mon_clock_drift_allowed=2s (CI: UDP 123 blocked)"
        echo ""
        echo "Verification:"
        echo "  verify_az_crush_map      Check rack CRUSH rule and az.nodeN buckets are present"
        echo "  verify_no_az_on_upgrade  Check upgrade from pre-AZ revision does not activate AZ CRUSH rules"
    fi
fi
