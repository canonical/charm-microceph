#!/bin/bash

set -eux

NODES=${NODES:-3}

for ((i=1;i<=NODES;i++)); do
    lxc delete "node${i}" --force
done
