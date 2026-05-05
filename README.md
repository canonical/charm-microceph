<a href="https://charmhub.io/microceph">
    <img alt="" src="https://charmhub.io/microceph/badge.svg" />
</a>
<br>
<a href="https://charmhub.io/microceph">
  <img alt="" src="https://charmhub.io/static/images/badges/en/charmhub-black.svg" />
</a>

# Overview

A charming way to deploy the microceph snap.

[MicroCeph](https://snapcraft.io/microceph) is a lightweight way to deploy Ceph cluster in
reliable and resilient distributed storage.

The microceph charm deploys the microceph snap and can scale out to form a
Ceph cluster with [Juju](https://juju.is/).

# Usage

Please visit [charmhub](https://charmhub.io/microceph) for documentation and instructions
on consuming charmed MicroCeph.

## Availability zones

The configures MicroCeph with Juju availability zones (AZs) during
cluster bootstrap. When the model provider exposes an AZ for a unit,
Juju sets `JUJU_AVAILABILITY_ZONE`; the charm passes that value to
MicroCeph. MicroCeph then uses the AZ as a CRUSH failure domain so
replicas can be distributed across zones when OSDs are added.

There is no charm configuration option required for this. Place the
MicroCeph units in the desired Juju zones when deploying or scaling
the application to enable this functionality.

AZ support is enabled only for clusters bootstrapped or adopted with a MicroCeph
snap that supports `--availability-zone`. Clusters upgraded from a charm or snap
that did not use AZ support are not converted retroactively, and later joining
units will not mix AZ and non-AZ membership.

You can verify the resulting topology after adding OSDs with:

```bash
juju status
juju ssh microceph/0 -- sudo microceph.ceph osd tree
```

## OSD device configuration

The charm can automatically enroll OSDs based on a declarative match expression.
Set the `osd-devices` config option with a DSL expression that the MicroCeph snap
uses to find block devices on each unit. 

Note: for safety reasons the matching is additive only. That is,
changes in this config option will only ever add new OSDs, existing
OSDs will never be removed as a result of config changes (or clearing
of configs).

For full DSL details, see the MicroCeph reference documentation:
https://canonical-microceph.readthedocs-hosted.com/latest/reference/commands/disk/#dsl-based-device-selection

Examples:

```
juju config microceph osd-devices="eq(@type,'nvme')"
juju config microceph osd-devices="and(eq(@type,'ssd'), ge(@size,100GiB))"
juju config microceph osd-devices="eq(@devnode,'/dev/sdb')"
```

To control device enrollment behavior, set `device-add-flags`:

- `wipe:osd` wipes non-pristine devices before enrollment.
- `encrypt:osd` enables encryption for matched devices.

Example:

```
juju config microceph device-add-flags="wipe:osd,encrypt:osd"
```

## Terraform module

A reusable Terraform + Terragrunt module for deploying the `microceph` charm can be found in `terraform/microceph/`. See the module README for usage instructions.

## Sunbeam end-to-end test

The repository also carries an attached-model Sunbeam test flow for the self-hosted CI runner and local reproduction. Sunbeam bootstraps the model and deploys the `microceph` application; the pytest suite attaches to that existing model, refreshes the app to the local charm, and exercises the post-refresh workflow:

```bash
./tests/scripts/bootstrap_sunbeam.sh
cp ~/artifacts/microceph.charm .
tox -e sunbeam -- --sunbeam-model sunbeam-controller:admin/openstack-machines
```

For a one-command local wrapper, use `./tests/scripts/run_sunbeam_e2e.sh` after placing the charm artifact at `./microceph.charm`.

