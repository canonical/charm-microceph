Adding OSDs
===========

OSDs are added by triggering ``add-osd action`` on each MicroCeph unit.
The unit's underlying machine is understood to house one or more storage devices.

1. List the available disks on the MicroCeph node:

   .. code-block:: none

      juju run microceph/0 list-disks

   The output of the above command should specify the OSDs that are already attached to MicroCeph
   and any unpartitioned disks that can be used to attach to MicroCeph as OSD.

   .. code-block:: none

      Running operation 1 with 1 task
        - task 2 on unit-microceph-0
      Waiting for task 2...
      osds: '[]'
      unpartitioned-disks: '[{''model'': '''', ''size'': ''10.00GiB'', ''type'': ''virtio'',
        ''path'': ''/dev/disk/by-id/virtio-71aa0fef-aec9-4129-9''}, {''model'': '''', ''size'':
        ''40.00GiB'', ''type'': ''virtio'', ''path'': ''/dev/disk/by-id/''}]'

2. Add the unpartitioned disks as OSD to the microceph cluster:

   .. code-block:: none

      juju run microceph/0 add-osd device-id=<DISK PATH>

   Multiple disks can be added in the ``add-osd`` action.

   .. code-block:: none

      juju run microceph/0 add-osd device-id=<DISK PATH>,<DISK PATH>

   The output of ``add-osd action`` should look similar to this:

   .. code-block:: none

      $ juju run microceph/0 add-osd device-id=/dev/disk/by-id/virtio-71aa0fef-aec9-4129-9
      Running operation 3 with 1 task
        - task 4 on unit-microceph-0
      Waiting for task 4...
      status: success

3. Verify if the disks are added as OSDs to the ceph cluster:

   .. code-block:: none

      juju run microceph/0 list-disks

   Now the added disks should be visible in osds.
   Sample output of the above command:

   .. code-block:: none

      Running operation 5 with 1 task
        - task 6 on unit-microceph-0
      Waiting for task 6...
      osds: '[{''osd'': ''0'', ''location'': ''microceph2'', ''path'': ''/dev/disk/by-id/virtio-71aa0fef-aec9-4129-9''}]'
      unpartitioned-disks: '[{''model'': '''', ''size'': ''40.00GiB'', ''type'': ''virtio'',
        ''path'': ''/dev/disk/by-id/''}]'

4. Run steps 1,2,3 on all the storage nodes.

5. Run ceph cluster status to check if OSDs are up:

   .. code-block:: none

      juju ssh microceph/leader sudo microceph.ceph status

   Sample output is:

   .. code-block:: none

        cluster:
          id:     edd914f5-fdf8-4b56-bdd7-95d6c5e10d81
          health: HEALTH_OK

        services:
          mon: 3 daemons, quorum microceph2,microceph3,microceph4 (age 12m)
          mgr: microceph2(active, since 13m), standbys: microceph3, microceph4
          osd: 3 osds: 3 up (since 34s), 3 in (since 56s)

        data:
          pools:   1 pools, 1 pgs
          objects: 2 objects, 577 KiB
          usage:   66 MiB used, 30 GiB / 30 GiB avail
          pgs:     1 active+clean

        io:
          client:   938 B/s rd, 43 KiB/s wr, 0 op/s rd, 1 op/s wr

   Now the ceph cluster is healthy and ready to use.
