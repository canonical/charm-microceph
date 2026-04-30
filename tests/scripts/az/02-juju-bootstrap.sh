#!/bin/bash

# Bootstrap a Juju controller from inside node1.
set -eux

CONTROLLER=${CONTROLLER:-lxd-cluster}

lxc exec node1 -- snap install juju
lxc exec node1 -- mkdir -p /root/.local/share/juju

# Bootstrap Juju controller against node1's clustered LXD daemon.
lxc exec node1 -- juju bootstrap lxd "${CONTROLLER}"

lxc exec node1 -- juju controllers
