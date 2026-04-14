#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper for local Sunbeam bootstrap plus the attached-model test
# suite. The local MicroCeph charm artifact is expected at ./microceph.charm.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUNBEAM_MODEL="${SUNBEAM_MODEL:-sunbeam-controller:admin/openstack-machines}"

"$REPO_ROOT/tests/scripts/bootstrap_sunbeam.sh"
cd "$REPO_ROOT"
tox -e sunbeam -- --sunbeam-model "$SUNBEAM_MODEL" "$@"
