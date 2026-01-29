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

