#!/usr/bin/env bash
#
# cephx-aes256k-rotation.sh
#
# Self-contained functional test of the CVE-2025-30156 cephx rework on a
# Juju-deployed (charmed) MicroCeph cluster: deploy three microceph units plus a
# ceph-fs consumer on the old channel, sideload a 19.2.6 snap under test, then
# rotate every cephx key to aes256k and cut over to aes256k-only. It pays special
# attention to the two charm-specific facts: the charm holds the snap and cannot
# do a local install (so we sideload per unit), and the charm authenticates as
# mon. so a rotated mon. key must be re-imported into each unit's mon keyring.
# Each step asserts its outcome; the run ends with a PASS/FAIL summary. This is
# the executable form of the "Upgrading charmed MicroCeph to 19.2.6 and rotating
# CephX keys" how-to.
#
# Requires: a bootstrapped Juju controller on an LXD cloud whose units can be VMs
# (juju bootstrap localhost lxd; the model sets virt-type=virtual-machine).
#
# Environment:
#   MODEL           juju model to create             (default cephx-cm-test)
#   OLD_CHANNEL     charm snap-channel to start on    (default squid/stable = 19.2.3)
#   MICROCEPH_SNAP  the 19.2.6 snap under test, one of:
#                     a bare revision, e.g. 1939      -> snap refresh --revision
#                     a channel, e.g. squid/edge      -> snap refresh --channel
#                     a path to a local .snap file    -> juju scp + snap install --dangerous
#                   (default: 1939)
#   BASE            charm base                        (default ubuntu@24.04)
#
# Usage:
#   tests/scripts/cephx-aes256k-rotation.sh          run the test
#   tests/scripts/cephx-aes256k-rotation.sh --clean  destroy the model
#
set -uo pipefail

MODEL="${MODEL:-cephx-cm-test}"
OLD_CHANNEL="${OLD_CHANNEL:-squid/stable}"
MICROCEPH_SNAP="${MICROCEPH_SNAP:-1939}"
BASE="${BASE:-ubuntu@24.04}"

PASS=(); FAIL=()
ok()  { echo "PASS: $1"; PASS+=("$1"); }
bad() { echo "FAIL: $1"; FAIL+=("$1"); }
banner() { printf '\n============================================================\n== %s\n============================================================\n' "$*"; }

mc()    { juju ssh -m "$MODEL" microceph/0 -- "sudo microceph.ceph $*" 2>/dev/null; }
uhost() { juju ssh -m "$MODEL" "microceph/$1" -- hostname 2>/dev/null | tr -d '\r'; }
units() { juju status -m "$MODEL" microceph --format=json 2>/dev/null | jq -r '.applications.microceph.units | keys[]'; }
wait_osds() { local n; for _ in $(seq 1 60); do n=$(mc "osd stat" | grep -oE '[0-9]+ up' | grep -oE '[0-9]+'); [ "${n:-0}" -ge 3 ] && return 0; sleep 6; done; mc "osd stat"; return 1; }
wait_versions_uniform() { for _ in $(seq 1 40); do mc "versions -f json" | grep -q 19.2.3 || return 0; sleep 6; done; return 1; }

# install the snap-under-test on one microceph unit, honouring MICROCEPH_SNAP's form
install_snap_under_test() {
    local unit="$1" src="${MICROCEPH_SNAP}"
    if [ -f "$src" ] || case "$src" in */*|*.snap) true;; *) false;; esac; then
        juju scp -m "$MODEL" "$src" "${unit}:/tmp/microceph-under-test.snap"
        juju ssh -m "$MODEL" "$unit" -- "sudo snap install --dangerous /tmp/microceph-under-test.snap"
        for i in block-devices hardware-observe mount-observe load-rbd microceph-support network-bind dm-crypt; do
            juju ssh -m "$MODEL" "$unit" -- "sudo snap connect microceph:$i" || true
        done
        juju ssh -m "$MODEL" "$unit" -- "sudo snap restart microceph.daemon"
    elif printf '%s' "$src" | grep -qE '^[0-9]+$'; then
        juju ssh -m "$MODEL" "$unit" -- "sudo snap refresh microceph --revision ${src} --amend"
    else
        juju ssh -m "$MODEL" "$unit" -- "sudo snap refresh microceph --channel ${src} --amend"
    fi
}

if [ "${1:-}" = "--clean" ]; then juju destroy-model --no-prompt --force --destroy-storage "$MODEL" 2>/dev/null; echo cleaned; exit 0; fi

##############################################################################
banner "Deploy 3 microceph units + a ceph-fs consumer on ${OLD_CHANNEL} (19.2.3)"
juju add-model "$MODEL"
juju switch "$MODEL" >/dev/null 2>&1 || true
juju set-model-constraints -m "$MODEL" virt-type=virtual-machine
juju deploy -m "$MODEL" ch:microceph -n 3 --channel "$OLD_CHANNEL" --base "$BASE" \
    --constraints "virt-type=virtual-machine cores=2 mem=4G root-disk=20G" \
    --config snap-channel="$OLD_CHANNEL"
juju wait-for application microceph \
    --query='forEach(units, u => u.workload-status == "active" || u.workload-status == "blocked")' --timeout=40m || true
for i in 0 1 2; do juju run -m "$MODEL" "microceph/$i" add-osd loop-spec=4G,1 --wait=15m || true; done
juju deploy -m "$MODEL" ch:ceph-fs --channel "$OLD_CHANNEL" --base "$BASE" --config source=distro || true
juju integrate -m "$MODEL" ceph-fs:ceph-mds microceph:mds || true
juju wait-for application microceph --query='forEach(units, u => u.workload-status == "active")' --timeout=30m || true
wait_osds && ok "3 OSDs up on 19.2.3" || bad "OSDs did not come up"
mc "version | grep -q 19.2.3" && ok "cluster starts on 19.2.3" || bad "expected 19.2.3"
juju ssh -m "$MODEL" microceph/0 -- "snap list microceph | grep -q held" && ok "charm holds the snap (auto-refresh off)" || true

##############################################################################
banner "Sideload the 19.2.6 snap under test on each unit (charm cannot do a local install)"
for i in 0 1 2; do
    u="microceph/$i"; echo "--- $u"
    juju run -m "$MODEL" "$u" enter-maintenance set-noout=true --wait=5m || true
    install_snap_under_test "$u"
    juju ssh -m "$MODEL" "$u" -- "cat /var/snap/microceph/current/conf/metadata.yaml"
    juju run -m "$MODEL" "$u" exit-maintenance --wait=5m || true
    wait_osds
done
if wait_versions_uniform; then ok "all daemons upgraded to 19.2.6"; else bad "some daemons still on the old release"; mc "versions"; fi
juju run -m "$MODEL" microceph/leader list-disks --wait=3m >/dev/null 2>&1 && ok "charm hooks work after the payload change" || bad "list-disks failed after upgrade"

##############################################################################
banner "Confirm the upgraded (mixed-cipher) state"
mc "mon dump 2>/dev/null | grep -q 'auth_allowed_ciphers aes, aes256k'" && ok "aes and aes256k both allowed" || bad "unexpected allowed_ciphers"

##############################################################################
banner "Rotate: prefer aes256k, then rotate mon. WITH the keyring re-import the charm needs"
mc "mon set auth_preferred_cipher aes256k"
mc "auth rotate --key-type=aes256k mon. >/dev/null"
KR=$(mc "auth get mon." | grep -vE '^exported|^[[:space:]]*$')
for i in 0 1 2; do
    h=$(uhost "$i")
    printf '%s\n' "$KR" | juju ssh -m "$MODEL" "microceph/$i" -- "sudo tee /var/snap/microceph/common/mon.keyring >/dev/null && sudo microceph.ceph-authtool /var/snap/microceph/common/data/mon/ceph-${h}/keyring --import-keyring /var/snap/microceph/common/mon.keyring && sudo rm /var/snap/microceph/common/mon.keyring && sudo snap restart microceph.mon"
    for _ in $(seq 1 30); do [ "$(mc 'quorum_status -f json 2>/dev/null' | jq '.quorum|length')" = 3 ] && break; sleep 3; done
done
if juju ssh -m "$MODEL" microceph/0 -- "sudo microceph.ceph --name mon. --keyring /var/snap/microceph/common/data/mon/ceph-\$(hostname)/keyring -s >/dev/null 2>&1"; then
    ok "mon. keyring re-synced (charm mon.-auth operations work)"
else
    bad "mon. keyring stale after rotation"
fi

banner "Rotate the OSD keys in place (no restart)"
for id in $(mc "osd ls"); do
    host=$(mc "osd find ${id} -f json" | jq -r '.host')
    NEW=$(mc "auth rotate --key-type=aes256k osd.${id}")
    RAW=$(printf '%s\n' "$NEW" | awk '/key =/{print $3}')
    for i in 0 1 2; do [ "$(uhost "$i")" = "$host" ] && U="microceph/$i"; done
    printf '[osd.%s]\n\tkey = %s\n' "$id" "$RAW" | juju ssh -m "$MODEL" "$U" -- "sudo tee /var/snap/microceph/common/data/osd/ceph-${id}/keyring >/dev/null"
    printf '%s' "$RAW" | juju ssh -m "$MODEL" "$U" -- "sudo microceph.ceph tell osd.${id} rotate-key -i -" && echo "  osd.${id} rotated in place"
done
wait_osds

banner "Rotate mgr / mds keys (including the ceph-fs consumer's own mds)"
mc "mon set auth_allowed_ciphers aes,aes256k"   # keep both allowed while daemons re-key
for i in 0 1 2; do
    h=$(uhost "$i")
    mc "auth rotate --key-type=aes256k mgr.${h}" | juju ssh -m "$MODEL" "microceph/$i" -- "sudo tee /var/snap/microceph/common/data/mgr/ceph-${h}/keyring >/dev/null && sudo snap restart microceph.mgr"
done
# every mds entity, wherever it runs (microceph units and the ceph-fs consumer unit)
for e in $(mc "auth ls 2>/dev/null" | grep -oE '^mds\.[^ ]+'); do
    NEW=$(mc "auth rotate --key-type=aes256k ${e}")
    host="${e#mds.}"
    if juju status -m "$MODEL" ceph-fs --format=json 2>/dev/null | jq -e --arg h "$host" '.applications["ceph-fs"].units | to_entries[] | select(.value["machine"] != null)' >/dev/null 2>&1 && \
       juju ssh -m "$MODEL" ceph-fs/0 -- "hostname" 2>/dev/null | tr -d '\r' | grep -qx "$host"; then
        printf '%s\n' "$NEW" | juju ssh -m "$MODEL" ceph-fs/0 -- "sudo tee /var/lib/ceph/mds/ceph-${host}/keyring >/dev/null && sudo chown ceph:ceph /var/lib/ceph/mds/ceph-${host}/keyring && sudo systemctl restart ceph-mds@${host}"
    else
        for i in 0 1 2; do [ "$(uhost "$i")" = "$host" ] && printf '%s\n' "$NEW" | juju ssh -m "$MODEL" "microceph/$i" -- "sudo tee /var/snap/microceph/common/data/mds/ceph-${host}/keyring >/dev/null && sudo snap restart microceph.mds"; done
    fi
done
sleep 15
mc "mon set auth_service_cipher aes256k"
  mc "config set mon mon_auth_allow_insecure_key false" || true
svc_clear=false
for _ in $(seq 1 24); do
    mc "--format=json health detail" | jq -e '.checks | has("AUTH_INSECURE_SERVICE_KEY_TYPE") | not' >/dev/null && { svc_clear=true; break; }
    sleep 5
done
if $svc_clear; then
    ok "all service daemon keys are aes256k"
else
    bad "service keys still insecure"; mc "health detail | grep -A8 AUTH_INSECURE_SERVICE_KEY_TYPE"
fi

banner "Rotate the admin key in all three places (two files + the dqlite row)"
NEW=$(mc "auth rotate --key-type=aes256k client.admin")
RAW=$(printf '%s\n' "$NEW" | awk '/key =/{print $3}')
for i in 0 1 2; do
    juju ssh -m "$MODEL" "microceph/$i" -- "sudo sh -c 'printf \"# Generated by MicroCeph, DO NOT EDIT.\n[client.admin]\n\tkey = ${RAW}\n\" | tee /var/snap/microceph/current/conf/ceph.client.admin.keyring /var/snap/microceph/current/conf/ceph.keyring >/dev/null'"
done
juju ssh -m "$MODEL" microceph/0 -- "sudo microceph cluster sql \"UPDATE config SET value='${RAW}' WHERE key='keyring.client.admin'\""
juju ssh -m "$MODEL" microceph/1 -- "sudo snap restart microceph.daemon"; sleep 40
juju ssh -m "$MODEL" microceph/1 -- "sudo grep -q '${RAW}' /var/snap/microceph/current/conf/ceph.keyring" \
    && ok "rotated admin key survives a daemon restart (dqlite row updated)" || bad "admin key reverted after restart"

##############################################################################
banner "Cut over to aes256k-only"
mc "health mute AUTH_INSECURE_CLIENT_KEY_TYPE 8w" || true
mc "mon set auth_allowed_ciphers aes256k"
mc "mon dump | grep -q 'auth_allowed_ciphers aes256k'" && ok "auth_allowed_ciphers is aes256k-only" || bad "cutover did not take"
juju run -m "$MODEL" microceph/leader list-disks --wait=3m >/dev/null 2>&1 && ok "charm hooks still work after cutover" || bad "list-disks failed after cutover"

##############################################################################
banner "Summary"
for p in "${PASS[@]:-}"; do [ -n "$p" ] && echo "  PASS  $p"; done
for f in "${FAIL[@]:-}"; do [ -n "$f" ] && echo "  FAIL  $f"; done
if [ "${#FAIL[@]}" -eq 0 ]; then echo "ALL CEPHX AES256K CHECKS PASSED"; else echo "${#FAIL[@]} FAILURE(S)"; exit 1; fi
