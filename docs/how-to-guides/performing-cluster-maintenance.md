# Perform cluster maintenance

MicroCeph provides a simple and consistent workflow to support cluster maintenance activity.

## Prerequisites

Cluster maintenance requires extra redundancy in ceph services, make sure you have

- at least 4 units of MicroCeph
- enabled Ceph Monitor on all units.

For example, a four-node MicroCeph cluster would look something similar to this:

```text
Model      Controller  Cloud/Region         Version  SLA          Timestamp
microceph  overlord    localhost/localhost  3.6.3    unsupported  14:09:58+08:00

App        Version  Status  Scale  Charm      Channel      Rev  Exposed  Message
microceph           active      4  microceph  latest/edge  103  no

Unit          Workload  Agent  Machine  Public address  Ports  Message
microceph/0   active    idle   0        10.42.75.217
microceph/1*  active    idle   1        10.42.75.46
microceph/2   active    idle   2        10.42.75.23
microceph/3   active    idle   3        10.42.75.189

Machine  State    Address       Inst id        Base          AZ  Message
0        started  10.42.75.217  juju-681fff-0  ubuntu@24.04      Running
1        started  10.42.75.46   juju-681fff-1  ubuntu@24.04      Running
2        started  10.42.75.23   juju-681fff-2  ubuntu@24.04      Running
3        started  10.42.75.189  juju-681fff-3  ubuntu@24.04      Running

MicroCeph deployment summary:
- juju-681fff-0 (10.42.75.217)
  Services: mds, mgr, mon, osd
  Disks: 1
- juju-681fff-1 (10.42.75.46)
  Services: mds, mgr, mon, osd
  Disks: 1
- juju-681fff-2 (10.42.75.23)
  Services: mds, mgr, mon, osd
  Disks: 1
- juju-681fff-3 (10.42.75.189)
  Services: mon, osd
  Disks: 1
```

## Review the action plan of maintenance mode

The action plan for entering or exiting the maintenance mode can reviewed by using the run dry-run option.

```shell
juju run microceph/leader exit-maintenance dry-run=True
juju run microceph/leader enter-maintenance dry-run=True
```

Some steps in the action plan can be optionally added or removed. To see what steps are optional, run:

```shell
juju show-action microceph exit-maintenance
juju show-action microceph enter-maintenance
```

## Enter maintenance mode

To put unit `microceph/3` into maintenance mode, and optionally disable the OSD service on that node, run

```shell
juju run microceph/3 enter-maintenance stop-osds=True
```

Our sample output looks like this:

```text
Running operation 15 with 1 task
  - task 16 on unit-microceph-3

Waiting for task 16...
actions:
  step-1:
    description: Check if osds.[4] in node 'juju-681fff-3' are ok-to-stop.
    error: ""
    id: check-osd-ok-to-stop-ops
  step-2:
    description: Check if there are at least 3 mon, 1 mds, and 1 mgr services in the
      cluster besides those in node 'juju-681fff-3'
    error: ""
    id: check-non-osd-svc-enough-ops
  step-3:
    description: Run `ceph osd set noout`.
    error: ""
    id: set-noout-ops
  step-4:
    description: Assert osd has 'noout' flag set.
    error: ""
    id: assert-noout-flag-set-ops
  step-5:
    description: Stop osd service in node 'juju-681fff-3'.
    error: ""
    id: stop-osd-ops
errors: ""
status: success
```

After entering maintenance mode, this is the status of the cluster:

```text
$ juju ssh microceph/3 -- sudo snap services microceph
Service               Startup   Current   Notes
microceph.daemon      enabled   active    -
microceph.mds         disabled  inactive  -
microceph.mgr         disabled  inactive  -
microceph.mon         enabled   active    -
microceph.osd         disabled  inactive  -
microceph.rbd-mirror  disabled  inactive  -
microceph.rgw         disabled  inactive  -

$ juju ssh microceph/3 -- sudo microceph.ceph -s
  cluster:
    id:     156f1fa0-51a5-4515-a538-2fe32a8803b5
    health: HEALTH_WARN
            noout flag(s) set
            1 osds down
            1 host (1 osds) down
            Degraded data redundancy: 2/6 objects degraded (33.333%), 1 pg degraded, 1 pg undersized

  services:
    mon: 4 daemons, quorum juju-681fff-1,juju-681fff-0,juju-681fff-2,juju-681fff-3 (age 25m)
    mgr: juju-681fff-1(active, since 25m), standbys: juju-681fff-0, juju-681fff-2
    osd: 4 osds: 3 up (since 100s), 4 in (since 24m)
         flags noout

  data:
    pools:   1 pools, 1 pgs
    objects: 2 objects, 449 KiB
    usage:   107 MiB used, 16 GiB / 16 GiB avail
    pgs:     2/6 objects degraded (33.333%)
             1 active+undersized+degraded
```

Compare the status of the cluster with the cluster status **before** entering maintenance mode:

```text
$ juju ssh microceph/3 -- sudo snap services microceph
Service               Startup   Current   Notes
microceph.daemon      enabled   active    -
microceph.mds         disabled  inactive  -
microceph.mgr         disabled  inactive  -
microceph.mon         enabled   active    -
microceph.osd         enabled   active    -
microceph.rbd-mirror  disabled  inactive  -
microceph.rgw         disabled  inactive  -

$ juju ssh microceph/3 -- sudo microceph.ceph -s
  cluster:
    id:     156f1fa0-51a5-4515-a538-2fe32a8803b5
    health: HEALTH_OK

  services:
    mon: 4 daemons, quorum juju-681fff-1,juju-681fff-0,juju-681fff-2,juju-681fff-3 (age 21m)
    mgr: juju-681fff-1(active, since 22m), standbys: juju-681fff-0, juju-681fff-2
    osd: 4 osds: 4 up (since 20m), 4 in (since 20m)

  data:
    pools:   1 pools, 1 pgs
    objects: 2 objects, 449 KiB
    usage:   107 MiB used, 16 GiB / 16 GiB avail
    pgs:     1 active+clean
```

> [!Note]
> The `microceph.osd` service is disabled and inactive after entering maintenance mode; the cluster also has noout flag
> set.

## Exit maintenance mode for microceph node

To recover unit `microceph/3` from maintenance mode, run

```shell
juju run microceph/3 exit-maintenance
```

Our sample output looks like this:

```text
$ juju run microceph/3 exit-maintenance
Running operation 17 with 1 task
  - task 18 on unit-microceph-3

Waiting for task 18...
actions:
  step-1:
    description: Run `ceph osd unset noout`.
    error: ""
    id: unset-noout-ops
  step-2:
    description: Assert osd has 'noout' flag unset.
    error: ""
    id: assert-noout-flag-unset-ops
  step-3:
    description: Start osd service in node 'juju-681fff-3'.
    error: ""
    id: start-osd-ops
errors: ""
status: success
```

This is the cluster status after exiting maintenance node for unit `microceph/3`

```text
$ juju ssh microceph/3 -- sudo snap services microceph
Service               Startup   Current   Notes
microceph.daemon      enabled   active    -
microceph.mds         disabled  inactive  -
microceph.mgr         disabled  inactive  -
microceph.mon         enabled   active    -
microceph.osd         enabled   active    -
microceph.rbd-mirror  disabled  inactive  -
microceph.rgw         disabled  inactive  -

$ juju ssh microceph/3 -- sudo microceph.ceph -s
  cluster:
    id:     156f1fa0-51a5-4515-a538-2fe32a8803b5
    health: HEALTH_OK

  services:
    mon: 4 daemons, quorum juju-681fff-1,juju-681fff-0,juju-681fff-2,juju-681fff-3 (age 30m)
    mgr: juju-681fff-1(active, since 31m), standbys: juju-681fff-0, juju-681fff-2
    osd: 4 osds: 4 up (since 45s), 4 in (since 29m)

  data:
    pools:   1 pools, 1 pgs
    objects: 2 objects, 449 KiB
    usage:   508 MiB used, 16 GiB / 16 GiB avail
    pgs:     1 active+clean
```

> [!Note]
> The `microceph.osd` service is enabled and active again after exiting maintenance mode; the cluster also does not have
> noout flag set.
