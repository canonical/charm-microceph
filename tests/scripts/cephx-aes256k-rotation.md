# CephX aes256k upgrade + rotation (CVE-2025-30156)

19.2.6 adds the `aes256k` cephx key type. Upgrade alone does not fix it — rotate every key, then disallow `aes`.

The charm holds the snap and cannot install a local file, so sideload per unit. The charm authenticates as `mon.`, so a rotated `mon.` key must be re-imported into each unit's mon keyring.

Kernel clients need Linux 7.0+ (Noble HWE 7.0). Do not rotate a kernel-client key, or cut over, while a client is on an older kernel.

## Run the test

```
tests/scripts/cephx-aes256k-rotation.sh
# snap under test defaults to revision 1939; for a local build:
MICROCEPH_SNAP=/path/to/microceph_19.2.6.snap tests/scripts/cephx-aes256k-rotation.sh
```

## Manual sequence

Pre: `juju ssh microceph/0 -- sudo microceph.ceph -s`   # HEALTH_OK

Upgrade each unit:

```
juju run microceph/N enter-maintenance set-noout=true   # may report "cannot be safely stopped" on a 3-OSD cluster; proceed
# a released 19.2.6 revision/channel (the charm holds the snap, so --amend):
juju ssh microceph/N -- sudo snap refresh microceph --revision <rev> --amend   # or --channel <chan> --amend
# or a local build:
juju scp <snap> microceph/N:/tmp/microceph.snap
juju ssh microceph/N -- sudo snap install --dangerous /tmp/microceph.snap
juju ssh microceph/N -- "for i in block-devices hardware-observe mount-observe load-rbd microceph-support network-bind dm-crypt; do sudo snap connect microceph:$i; done; sudo snap restart microceph.daemon"
juju run microceph/N exit-maintenance
# do NOT change snap-channel afterwards
```

Confirm: `juju ssh microceph/0 -- sudo microceph.ceph versions`   # all 19.2.6; health shows AUTH_INSECURE_* (expected)

Rotate — run each `microceph.ceph ...` on a microceph unit (`juju ssh microceph/0 -- <cmd>`):

```
sudo microceph.ceph mon set auth_preferred_cipher aes256k
# mon.: rotate, then on EACH unit import the new key into its mon keyring and restart microceph.mon.
# stage under /var/snap/microceph/common — the snap's ceph-authtool cannot read the host /tmp:
sudo microceph.ceph auth rotate --key-type=aes256k mon.
sudo microceph.ceph-authtool /var/snap/microceph/common/data/mon/ceph-<host>/keyring --import-keyring /var/snap/microceph/common/mon.keyring
# osd: rotate, write the [osd.N] key stanza to .../common/data/osd/ceph-N/keyring, then rotate in place (no restart):
sudo microceph.ceph auth rotate --key-type=aes256k osd.N
printf '%s' <raw-key> | sudo microceph.ceph tell osd.N rotate-key -i -   # -i - reads the raw key from stdin
# mgr/mds: rotate, write .../common/data/{mgr,mds}/ceph-<host>/keyring, restart microceph.mgr / microceph.mds
sudo microceph.ceph mon set auth_service_cipher aes256k     # then wait for AUTH_INSECURE_SERVICE_KEY_TYPE to clear
sudo microceph.ceph config set mon mon_auth_allow_insecure_key false
# admin = 3 places: both conf keyring files + the dqlite row. Back up client.admin first (recovery is painful):
sudo microceph.ceph auth rotate --key-type=aes256k client.admin
sudo microceph cluster sql "UPDATE config SET value='<raw-key>' WHERE key='keyring.client.admin'"
# a related ceph-fs consumer runs its own mds.<host>: rotate it on the ceph-fs unit
# (write /var/lib/ceph/mds/ceph-<host>/keyring, chown ceph:ceph, systemctl restart ceph-mds@<host>)
sudo microceph.ceph auth rotate --key-type=aes256k client.X
```

Cut over:

```
sudo microceph.ceph mon set auth_allowed_ciphers aes256k
```

Rescue: `mon_auth_emergency_allowed_ciphers` in a mon's local config re-admits `aes` temporarily.

## Upstream

- CVE-2025-30156: https://docs.ceph.com/en/latest/security/CVE-2025-30156/
- Rotation procedure: https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/#upgrading-and-rotating-cephx-keys
- Health checks: https://docs.ceph.com/en/latest/rados/operations/health-checks/
- Emergency allowed ciphers: https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/#emergency-allowed-ciphers
- Squid 19.2.6 notes: https://docs.ceph.com/en/latest/releases/squid/#v19-2-6-squid
- MicroCeph upgrade: `docs/how-to/major-upgrade.rst`
