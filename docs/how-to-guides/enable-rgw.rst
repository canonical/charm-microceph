Enable/Disable Ceph RADOS Gateway
===================================

Enable/Disable Ceph RADOS Gateway (RGW) by setting the config option ``enable-rgw``.

1. Enable RGW service

   .. code-block:: none

      juju config microceph enable-rgw="*"

2. Check MicroCeph status

   .. code-block:: none

      juju ssh microceph/leader sudo microceph status

   The output of the above command should list RGW as part of services
   running on each node.
   Sample output is:

   .. code-block:: none

      MicroCeph deployment summary:
      - microceph2 (10.121.193.184)
        Services: mds, mgr, mon, rgw, osd
        Disks: 1
      - microceph3 (10.121.193.185)
        Services: mds, mgr, mon, rgw, osd
        Disks: 1
      - microceph4 (10.121.193.186)
        Services: mds, mgr, mon, rgw, osd
        Disks: 1

3. Run `ceph cluster status` to check if RGW daemon is running

   .. code-block:: none

      juju ssh microceph/leader sudo microceph.ceph status

   The output of the above command should list RGW under services.
   Sample output is:

   .. code-block:: none

        cluster:
          id:     edd914f5-fdf8-4b56-bdd7-95d6c5e10d81
          health: HEALTH_OK

        services:
          mon: 3 daemons, quorum microceph2,microceph3,microceph4 (age 12m)
          mgr: microceph2(active, since 13m), standbys: microceph3, microceph4
          osd: 3 osds: 3 up (since 34s), 3 in (since 56s)
          rgw: 3 daemons, quorum microceph2,microceph3,microceph4 (age 30s)

        data:
          pools:   5 pools, 5 pgs
          objects: 2 objects, 577 KiB
          usage:   66 MiB used, 30 GiB / 30 GiB avail
          pgs:     5 active+clean

        io:
          client:   938 B/s rd, 43 KiB/s wr, 0 op/s rd, 1 op/s wr

   Now the ceph cluster is healthy and ready to use.

4. Disable RGW service

   .. code-block:: none

      juju config microceph enable-rgw=""
