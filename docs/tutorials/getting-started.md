This guide contains the essential steps for deploying a Charm microceph to build a Ceph cluster.

### What you will need

- A snapd-compatible host to run the [Juju client](https://juju.is/docs/installing)
- A [MAAS cluster](https://maas.io/install) (with a user account at your disposal)

### Procedure

Run the below commands on the host allocated to the Juju client.

Install the Juju client:

    sudo snap install juju --channel 3.1/stable

Inform the Juju client about the MAAS cluster (choose 'maas' during the interactive session):

    juju add-cloud --client

Add your MAAS user's API key:

    juju add-credential my-maas

Create a Juju controller to manage the Ceph deployment:

    juju bootstrap my-maas my-controller

Create a model

    juju add-model microceph

Deploy the microceph nodes:

    juju deploy -n 3 microceph --channel latest/edge

Monitor the deployment:

    watch -c juju status --color

List OSDs and available unpartitioned disks on each node:

    juju run microceph/0 list-disks --host-only

Add OSDs on each node:

    juju run microceph/0 add-osd <Unpartitioned DISK PATH>

Check ceph status:

    juju run microceph/leader sudo microceph.ceph status

You now have a Ceph cluster up and running.
