#!/bin/bash
# Run the full microceph charm exercise sequence.
#
# Assumes 01-lxd-cluster-setup.sh and 02-juju-bootstrap.sh have already run.
# Override any variable by setting it in the environment before invoking.

set -eux

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

export NODES=${NODES:-3}
export CONTROLLER=${CONTROLLER:-lxd-cluster}
export MODEL=${MODEL:-microceph}
export BASE_CHANNEL=${BASE_CHANNEL:-ubuntu@24.04}
export CHARM_PATH=${CHARM_PATH:-${REPO_ROOT}/microceph_ubuntu-24.04-amd64.charm}
export SNAP_CHANNEL=${SNAP_CHANNEL:-squid/edge}
export CHARM_REVISION=${CHARM_REVISION:-227}
export CHARM_TRACK=${CHARM_TRACK:-squid/stable}

# shellcheck source=03-exercise-microceph.sh
source "${SCRIPT_DIR}/03-exercise-microceph.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

header() {
    echo ""
    echo "========================================================================"
    echo "  $*"
    echo "  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "========================================================================"
}

pass() {
    echo ""
    echo ">>> PASSED: $*"
}

# Clean up any model left over from a previous interrupted run.
header "Initial cleanup (idempotent)"
prune_units

# ---------------------------------------------------------------------------
# Test 1: Deploy from charm file → add OSDs → verify AZ CRUSH map
# ---------------------------------------------------------------------------

header "TEST 1 — deploy from charm file, verify AZ CRUSH map"
echo "  CHARM_PATH=${CHARM_PATH}"
echo "  NODES=${NODES}"

header "  [1/5] deploy_from_charm_file"
deploy_from_charm_file

header "  [2/5] relax_clock_skew"
relax_clock_skew

header "  [3/5] juju status (post-deploy)"
_juju status

header "  [4/5] add_osd_loop"
add_osd_loop

header "  [5/5] verify_az_crush_map"
verify_az_crush_map

pass "TEST 1"

# ---------------------------------------------------------------------------
# Cleanup between tests
# ---------------------------------------------------------------------------

header "  Cleanup: prune_units"
prune_units

# ---------------------------------------------------------------------------
# Test 2: Deploy from revision → add OSDs → upgrade → verify no AZ on upgrade
# ---------------------------------------------------------------------------

header "TEST 2 — deploy from revision ${CHARM_REVISION} (${CHARM_TRACK}), upgrade, verify no AZ activated"
echo "  CHARM_REVISION=${CHARM_REVISION}"
echo "  CHARM_TRACK=${CHARM_TRACK}"
echo "  CHARM_PATH (upgrade target)=${CHARM_PATH}"
echo "  NODES=${NODES}"

header "  [1/7] deploy_from_revision"
deploy_from_revision

header "  [2/7] relax_clock_skew"
relax_clock_skew

header "  [3/7] juju status (post-deploy)"
_juju status

header "  [4/7] add_osd_loop"
add_osd_loop

header "  [5/7] upgrade_to_charm_file"
upgrade_to_charm_file

header "  [6/7] verify_no_az_on_upgrade"
verify_no_az_on_upgrade

header "  [7/7] join_additional_unit"
join_additional_unit node3

pass "TEST 2"

# ---------------------------------------------------------------------------
# Final cleanup
# ---------------------------------------------------------------------------

header "  Cleanup: prune_units"
prune_units

header "ALL TESTS PASSED"
