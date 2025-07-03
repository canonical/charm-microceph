#!/bin/bash
set -euo pipefail
APP=$1
juju status "${APP}"/leader --format=json | jq -r '"{\"endpoint\": \"" + (.applications | .[].units | .[] | .["public-address"] // .address) + "\"}"'