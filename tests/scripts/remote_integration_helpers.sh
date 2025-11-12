#!/usr/bin/env bash

function wait_for_remote_enlistment() {
  set -eux
  local unit="${1?missing}"
  local remote="${2?missing}"

  declare -i i=0
  while [[ $i -lt 20 ]]; do
    remotes=$(juju ssh primary/0 -- "sudo microceph remote list --json")
    remote_enlisted=$(echo $remotes | jq --arg REMOTE "$remote" 'any(.name == $REMOTE)')
    if [[ $remote_enlisted == "true" ]]; then
      echo "Remote $remote enlistment successful."
      break
    fi
    echo "failed $i attempt, retrying in 5 seconds"
    sleep 30s
    i=$((i + 1))
  done

  if [[ $i -eq 20 ]]; then
    echo "Timeout reached, failed to enlist remote."
    exit -1
  fi
}

run="${1}"
shift

$run "$@"
