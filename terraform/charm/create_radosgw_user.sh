#!/bin/bash

user_id=$1
display_name=$2
model_name=$3

juju ssh microceph/leader -m "${model_name}" -- "sudo microceph.radosgw-admin user info --uid='${user_id}' --format=json 2>/dev/null || sudo microceph.radosgw-admin user create --uid='${user_id}' --display-name='${display_name}' --format=json" | jq -r 'to_entries | map({key: .key, value: (.value | tostring)}) | from_entries'
