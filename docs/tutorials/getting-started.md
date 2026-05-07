# Deploy MicroCeph via Charm

In this tutorial we will deploy a three node charm-microceph cluster in an LXD environment using Juju. We will set up LXD on a single physical machine, and use simulated storage to keep hardware requirements at a minimum. This works great for learning and testing purposes; in a production environment you would typically utilize Juju to deploy to several physical machines and storage media. 

First, we will install and configure LXD which will provide virtual machines for our cluster. Then we will install and bootstrap Juju, using LXD as a provider. Subsequently we will install and bootstrap the MicroCeph cluster, and in the final step configure (simulated) storage for MicroCeph.

## Prerequisites

To successfully follow this tutorial, you will need:

- a Linux machine with at least 16G of memory and 50G of free disk space
- with snapd installed
- and virtualization-enabled

Note that this tutorial might run into issues on a container (such as docker) as containers typically are lacking virtualization capabilities.


## Install and configure LXD

Install the LXD snap and auto-configure it -- this will give the host machine the ability to spawn VMs, including networking and storage:

```
$ sudo snap install lxd
$ sudo lxd init --auto
```

Note that depending on your system, LXD might come pre-installed.

## Install and bootstrap Juju

Install and then configure Juju to make use of the LXD provider that was setup in the previous step:

```
$ sudo snap install juju 
juju (3/stable) 3.6.8 from Canonical✓ installed

$ juju bootstrap localhost lxd-controller
Since Juju 3 is being run for the first time, it has downloaded the latest public cloud information.
Creating Juju controller "lxd-controller" on localhost/localhost
...
Now you can run
	juju add-model <model-name>
to create a new model to deploy workloads.
```

You have successfully created a Juju controller named `lxd-controller`. Notice that the juju bootstrap command prompts you to create a Juju model. Models are the logical grouping of connected applications in Juju. We will create a model `mymodel`:

```
$ juju add-model mymodel
Added 'mymodel' model on localhost/localhost with credential 'localhost' for user 'admin'
```

Configure the model to spawn VMs with 4G of memory and a disk of 16G:

```
$ juju set-model-constraints virt-type=virtual-machine mem=4G root-disk=16G
```


For further details consult the [Juju documentation on LXD](https://documentation.ubuntu.com/juju/3.6/reference/cloud/list-of-supported-clouds/the-lxd-cloud-and-juju/#the-lxd-cloud-and-juju)


## Deploy MicroCeph

With the Juju environment configured, the next step is to deploy MicroCeph. Deploy three MicroCeph units with this command:

```
$ juju deploy microceph --num-units 3
Deployed "microceph" from charm-hub charm "microceph", revision 155 in channel tentacle/edge on ubuntu@24.04/stable
```

Juju deploys the MicroCeph units in the background. This process might take a few minutes depending on network speed and available resources. Check progress by running the `juju status` command. Once the deployment is done, `juju status` will report 3 active units:

```
$ juju status
Model    Controller      Cloud/Region         Version  SLA          Timestamp
mymodel  lxd-controller  localhost/localhost  3.6.8    unsupported  11:15:26Z

App        Version  Status  Scale  Charm      Channel       Rev  Exposed  Message
microceph           active      3  microceph  tentacle/edge 155  no

Unit          Workload  Agent  Machine  Public address  Ports  Message
microceph/0   active    idle   0        10.106.25.67           
microceph/1   active    idle   1        10.106.25.66           
microceph/2*  active    idle   2        10.106.25.144          

Machine  State    Address        Inst id        Base          AZ  Message
0        started  10.106.25.67   juju-9fe08a-0  ubuntu@24.04      Running
1        started  10.106.25.66   juju-9fe08a-1  ubuntu@24.04      Running
2        started  10.106.25.144  juju-9fe08a-2  ubuntu@24.04      Running
```

Installing the MicroCeph units also bootstrapped a Ceph cluster. Check the status by SSHing into one unit and running `ceph -s`. This should result in something like the below:

```
$ juju ssh microceph/0 "sudo ceph -s"
  cluster:
    id:     e131a957-bb56-489c-bb10-1782cd29e5f2
    health: HEALTH_WARN
            OSD count 0 < osd_pool_default_size 3
 
  services:
    mon: 3 daemons, quorum juju-9fe08a-2,juju-9fe08a-0,juju-9fe08a-1 (age 77s)
    mgr: juju-9fe08a-2(active, since 118s), standbys: juju-9fe08a-0, juju-9fe08a-1
    osd: 0 osds: 0 up, 0 in
...
```

The above output shows a running Ceph cluster, however it displays a health warning. Ceph warns us because it by default expects three disks (OSDs in Ceph parlance) for storage, and we have not yet configured any.

## Adding disks

For the purposes of this tutorial we will be setting up small simulated disks for ease of configuration. Note these small loop disks are only suitable for a demo setup like this; in a production environment physical disks would be utilized instead.

Run this command to add loop based storage of 2G size to a unit:

```
$ juju add-storage microceph/0 osd-standalone="loop,2G,1"
added storage osd-standalone/0 to microceph/0
```

This will configure the first unit with a 2G OSD. When running `juju status` you should see the unit status change to `executing` and a status message appear that an OSD is being enrolled.

Continue by running the same command for the other two units:

```
juju add-storage microceph/1 osd-standalone="loop,2G,1"
juju add-storage microceph/2 osd-standalone="loop,2G,1"
```

It will take a few minutes to configure the storage for Ceph, but once done (the units will display a status of `active` / `idle`) the Ceph status should look something like this:

```
$ juju ssh microceph/0 "sudo ceph -s"
  cluster:
    id:     e131a957-bb56-489c-bb10-1782cd29e5f2
    health: HEALTH_OK
 
  services:
    mon: 3 daemons, quorum juju-9fe08a-2,juju-9fe08a-0,juju-9fe08a-1 (age 7m)
    mgr: juju-9fe08a-2(active, since 8m), standbys: juju-9fe08a-0, juju-9fe08a-1
    osd: 3 osds: 3 up (since 34s), 3 in (since 42s)
```

## Next steps

We have successfully set up a Juju-managed MicroCeph cluster, ready to serve as a test and learning environment. 
To see how to configure and integrate MicroCeph with other Juju applications, see [the MicroCeph page on Charmhub](https://charmhub.io/microceph).
