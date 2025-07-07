#!/bin/bash

user_id=$1
display_name=$2

juju ssh microceph/leader -- sudo microceph.radosgw-admin user create --uid="${user_id}" --display-name="${display_name}" | jq -r 'to_entries | map({key: .key, value: (.value | tostring)}) | from_entries'
