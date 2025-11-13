#!/usr/bin/env bash

function wait_for_remote_enlistment() {
  set -ux
  local unit="${1?missing}"
  local remote="${2?missing}"

  declare -i i=0
  while [[ $i -lt 40 ]]; do
    remotes=$(juju ssh $unit -- "sudo microceph remote list --json")
    if [[ $? -ne 0 ]]; then
      echo "remote list command failed, retrying in 5 seconds"
      sleep 60s
      i=$((i + 1))
      juju show-unit $unit
      continue
    fi

    # check for remote entry
    remote_enlisted=$(echo $remotes | jq --arg REMOTE "$remote" 'any(.name == $REMOTE)')
    if [[ $remote_enlisted == "true" ]]; then
      echo "Remote $remote enlistment successful."
      break
    fi

    echo "failed $i attempt, retrying in 5 seconds"
    sleep 30s
    i=$((i + 1))
  done

  if [[ $i -gt 30 ]]; then
    echo "Timeout reached, failed to enlist remote."
    exit -1
  fi
}

run="${1}"
shift

$run "$@"
