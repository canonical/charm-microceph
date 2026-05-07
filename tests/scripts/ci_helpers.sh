#!/usr/bin/env bash

function check_osd_count() {
    local unit="${1?missing}"
    local count="${2?missing}"

    echo $USER
    for i in $(seq 1 50); do
      osd_count=$(juju exec --unit ${unit} -- microceph disk list --json | jq '.ConfiguredDisks | length')
      if [[ $osd_count -ne $count ]] ; then
          echo "Expected OSDs $count, Actual ${osd_count}. waiting..."
          sleep 10s
      fi
    done

    # fail if the OSDs are still not settled.
    osd_count=$(juju exec --unit ${unit} -- microceph disk list --json | jq '.ConfiguredDisks | length')
    if [[ $osd_count -ne $count ]] ; then
        echo "Expected OSDs $count, Actual ${osd_count}"
        exit 1
    fi
    juju status
}

function install_deps() {
    date
    sudo apt-get -qq install jq tox
    sudo snap install juju
    mkdir -p ~/.local/share/juju
    juju bootstrap localhost lxd
    juju add-model microceph-test --config logging-config="<root>=INFO;unit=DEBUG"
    date
}

function install_terraform_tooling() {
  local terraform_version="${TERRAFORM_VERSION:-1.9.8}"
  local terragrunt_version="${TERRAGRUNT_VERSION:-0.67.16}"
  local arch
  local tmp_dir

  case "$(uname -m)" in
    x86_64)
      arch=amd64
      ;;
    aarch64 | arm64)
      arch=arm64
      ;;
    *)
      echo "Unsupported architecture: $(uname -m)"
      return 1
      ;;
  esac

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' RETURN

  if ! command -v terraform >/dev/null; then
    curl -fsSL \
      -o "$tmp_dir/terraform.zip" \
      "https://releases.hashicorp.com/terraform/${terraform_version}/terraform_${terraform_version}_linux_${arch}.zip" || return 1
    python3 -m zipfile -e "$tmp_dir/terraform.zip" "$tmp_dir" || return 1
    sudo install -m 0755 "$tmp_dir/terraform" /usr/local/bin/terraform || return 1
  fi

  if ! command -v terragrunt >/dev/null; then
    curl -fsSL \
      -o "$tmp_dir/terragrunt" \
      "https://github.com/gruntwork-io/terragrunt/releases/download/v${terragrunt_version}/terragrunt_linux_${arch}" || return 1
    sudo install -m 0755 "$tmp_dir/terragrunt" /usr/local/bin/terragrunt || return 1
  fi

  terraform version || return 1
  terragrunt --version || return 1
}


function validate_terragrunt_module() {
  set -eux
  local working_dir="terraform/microceph"
  export TF_VAR_model_uuid="${TF_VAR_model_uuid:-00000000-0000-0000-0000-000000000000}"
  export TF_IN_AUTOMATION=1

  pushd $working_dir
  TERRAGRUNT_NON_INTERACTIVE=true terragrunt init
  TERRAGRUNT_NON_INTERACTIVE=true terragrunt validate
}

function cleanup_docker() {
  sudo apt purge docker* --yes
  sudo apt purge containerd* --yes
  sudo apt autoremove --yes
  sudo rm -rf /run/containerd
}

function bootstrap_k8s() {
  sudo microk8s enable hostpath-storage
  sudo microk8s status --wait-ready
}

function bootstrap_k8s_controller() {
  set -eux
  sudo microk8s kubectl config view --raw | juju add-k8s localk8s --client
 
  juju bootstrap localk8s k8s --debug
}

function deploy_cos() {
  set -eux
  juju add-model cos --config logging-config="<root>=INFO;unit=DEBUG"
  juju deploy cos-lite --trust

  juju offer prometheus:receive-remote-write
  juju offer grafana:grafana-dashboard
  juju offer loki:logging

  juju wait-for application prometheus --query='name=="prometheus" && (status=="active" || status=="idle")' --timeout=10m
  juju wait-for application grafana --query='name=="grafana" && (status=="active" || status=="idle")' --timeout=10m
  juju wait-for application loki --query='name=="loki" && (status=="active" || status=="idle")' --timeout=10m
}

function deploy_microceph() {
  date
  mv ~/artifacts/microceph_ubuntu-24.04-amd64.charm ./microceph_ubuntu-24.04-amd64.charm
  juju switch lxd
  juju deploy ./tests/bundles/multi_node_juju_storage.yaml
  # wait for charm to bootstrap and OSD devices to enroll.
  ./tests/scripts/ci_helpers.sh wait_for_microceph_bootstrap
  juju status
  date
}

function deploy_grafana_agent() {
  set -eux
  date
  juju switch lxd
  juju deploy grafana-agent --base ubuntu@24.04
  juju integrate grafana-agent microceph

  # wait for grafana-agent to be ready for integration
  juju wait-for application grafana-agent --query='name=="grafana-agent" && (status=="blocked" || status=="idle")' --timeout=20m
  
  # Integrate with cos services
  juju integrate grafana-agent k8s:cos.prometheus
  juju integrate grafana-agent k8s:cos.grafana
  juju integrate grafana-agent k8s:cos.loki

  juju wait-for unit grafana-agent/1 --query='workload-message=="tracing: off"' --timeout=20m
}

function check_http_endpoints_up() {
  set -ux

  juju switch k8s
  prom_addr=$(juju status --format json | jq '.applications.prometheus.address' | tr -d "\"")
  graf_addr=$(juju status --format json | jq '.applications.grafana.address' | tr -d "\"")

  for i in $(seq 1 20); do
    prom_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$prom_addr:9090/graph")
    grafana_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$graf_addr:3000/login")
    if [[ $prom_http_code -eq 200 && $grafana_http_code -eq 200 ]]; then
      echo "Prometheus and Grafana HTTP endpoints are up"
      break
    fi
    echo "."
    sleep 30s
  done

  prom_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$prom_addr:9090/graph")
  if [[ $prom_http_code -ne 200 ]]; then
    echo "Prometheus HTTP endpoint not up: HTTP($prom_http_code)"
    exit 1
  fi

  grafana_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$graf_addr:3000/login")
  if [[ $grafana_http_code -ne 200 ]]; then
    echo "Grafana HTTP endpoint not up: HTTP($grafana_http_code)"
    exit 1
  fi
}

function verify_o11y_services() {
  set -eux
  date
  juju switch k8s
  prom_addr=$(juju status --format json | jq '.applications.prometheus.address' | tr -d "\"")
  graf_addr=$(juju status --format json | jq '.applications.grafana.address' | tr -d "\"")

  # verify prometheus metrics are populated
  curl_output=$(curl "http://${prom_addr}:9090/api/v1/query?query=ceph_health_detail")
  prom_status=$(echo $curl_output | jq '.status' | tr -d "\"")
  if [[ "$prom_status" != "success" ]]; then
    echo "Prometheus query for ceph_health_detail returned $curl_output"
    exit 1
  fi

  get_admin_action=$(juju run grafana/0 get-admin-password --format json --wait 5m)
  action_status=$(echo $get_admin_action | jq '."grafana/0".status' | tr -d "\"")
  if [[ $action_status != "completed" ]]; then
    echo "Failed to fetch admin password from grafana: $get_admin_action"
    exit 1
  fi

  grafana_pass=$(echo $get_admin_action | jq '."grafana/0".results."admin-password"' | tr -d "\"")

  # check if expected dashboards are populated in grafana
  expected_dashboard_count=$(wc -l < ./tests/scripts/assets/expected_dashboard.txt)
  for i in $(seq 1 20); do
    curl http://admin:${grafana_pass}@${graf_addr}:3000/api/search| jq '.[].title' | jq -s 'sort' > dashboards.json
    cat ./dashboards.json 

    # compare the dashboard outputs
    match_count=$(grep -F -c -f ./tests/scripts/assets/expected_dashboard.txt dashboards.json || true) 
    if [[ $match_count -eq $expected_dashboard_count ]]; then
      echo "Dashboards match expectations"
      break 
    fi
    echo "."
    sleep 1m
  done
  
  match_count=$(grep -F -c -f ./tests/scripts/assets/expected_dashboard.txt dashboards.json || true) 
  if [[ $match_count -ne $expected_dashboard_count ]]; then
    echo "Required dashboards still not present."
    cat ./dashboards.json
    exit 1
  fi
}

function install_juju_simple() {
    sudo snap install juju
    mkdir -p ~/.local/share/juju
    juju bootstrap localhost
}

function setup_juju_spaces() {
    set -ex
    date
    juju add-model microceph-test --config logging-config="<root>=INFO;unit=DEBUG"
    juju add-space cluster
    # Subnet value from LXD profile.
    juju move-to-space cluster 10.85.4.0/24
}

function ensure_osd_count_on_host() {
  set -ex
  local location="${1?missing}"
  local count="${2?missing}"

  disk_list=$(juju ssh microceph/leader -- "sudo microceph disk list --json")
  osd_count=$(echo $disk_list | jq -r '.ConfiguredDisks[].location' | grep -c $location || true)
  if [[ $osd_count -ne $count ]] ; then
    echo "Unexpected OSD count on node microceph/2 $osd_count"
    exit 1
  fi
}

function remove_unit_wait() {
  set -ex
  local unit_name="${1?missing}"

  juju remove-unit $unit_name --no-prompt
  # wait and check if the unit is still present.
  for i in $(seq 1 50); do
    res=$( ( juju status | grep -cF "$unit_name" ) || true )
    if [[ $res -gt 0 ]] ; then
      echo -n '.'
      sleep 10
    else
      echo "Unit removed successfully"
      break
    fi
  done
  # fail if unit still present.
  if [[ $res -gt 0 ]] ; then
    echo "Unit still present"
    juju status
    exit 1
  fi
}

function verify_juju_spaces_config() {
    set -ex
    date
    local public="10.196.231.0/24"
    local cluster="10.85.4.0/24"

    # Verify the cluster network is correct subnet.
    output=$(juju ssh microceph/0 -- "sudo microceph cluster config get cluster_network")
    echo $output | grep $cluster

    # Verify the public network is correct subnet.
    output=$(juju ssh microceph/0 -- "sudo microceph cluster sql 'select value from config where key is \"public_network\"'")
    echo $output | grep $public
}

function seed_lxd_profile() {
    set -ex
    date
    local file_path="${1?missing}"
    lxd init --verbose --preseed < $file_path
    lxc profile show default
    lxc network list
}

function prepare_environment() {
  set -ux
  date
  sudo snap install juju
  mkdir -p ~/.local/share/juju
  
  declare -i i=0
  while [[ $i -lt 20 ]]; do
      seed_lxd_profile ./tests/scripts/assets/lxd-preseed.yaml
      if [[ $? -eq 0 ]]; then
        echo "LXD profile seeding successful."
        break
      fi
      echo "failed $i attempt, retrying in 5 seconds";
      sleep 5s
      i=$((i + 1));
  done

  if [[ $i -eq 20 ]]; then
      echo "Timeout reached, failed to seed lxd profile."
      exit -1
  fi

  juju bootstrap localhost lxd
}

function wait_for_microceph_bootstrap() {
    # wait for install hook to trigger on one unit.
    juju wait-for unit microceph/0 --query='workload-message=="(install) (bootstrap) Service not bootstrapped"' --timeout=20m
    # Now wait for the model to settle.
    juju wait-for application microceph --query='name=="microceph" && (status=="active" || status=="idle")' --timeout=10m
}

function dump_microceph_cluster_diagnostics() {
  local app="${1:-microceph}"
  local unit="${2:-${app}/0}"

  echo "--- juju status (${app}) ---"
  juju status "$app" || juju status || true

  echo "--- microceph status (${unit}) ---"
  juju ssh "$unit" -- sudo microceph status || true

  echo "--- ceph status (${unit}) ---"
  juju ssh "$unit" -- sudo microceph.ceph -s || true

  echo "--- ceph health detail (${unit}) ---"
  juju ssh "$unit" -- sudo microceph.ceph health detail || true
}

function wait_for_ceph_health_ok() {
  local app="${1:-microceph}"
  local unit="${2:-${app}/0}"
  local settle_timeout="${3:-20m}"
  local health_timeout="${4:-20m}"
  local poll_interval="${5:-15}"

  echo "Waiting for ${app} units to become active/idle (timeout: ${settle_timeout})"
  juju wait-for application "$app" \
    --query='forEach(units, unit => unit.workload-status=="active" && unit.agent-status=="idle")' \
    --timeout="$settle_timeout"

  echo "Waiting for Ceph health on ${unit} to become HEALTH_OK (timeout: ${health_timeout})"
  if timeout "$health_timeout" bash -c '
    unit="$1"
    poll_interval="$2"
    last_health=""

    while true; do
      if status=$(juju ssh "$unit" -- sudo microceph.ceph status --format json 2>/dev/null); then
        health=$(jq -r ".health.status // empty" <<< "$status" 2>/dev/null || true)
        if [[ -z "$health" ]]; then
          echo "Unable to parse Ceph status JSON from ${unit}, retrying..."
        else
          if [[ "$health" != "$last_health" ]]; then
            echo "Current Ceph health for ${unit}: ${health}"
            last_health="$health"
          fi
          if [[ "$health" == "HEALTH_OK" ]]; then
            exit 0
          fi
        fi
      else
        echo "Unable to fetch Ceph status from ${unit}, retrying..."
      fi

      sleep "$poll_interval"
    done
  ' bash "$unit" "$poll_interval"; then
    juju ssh "$unit" -- sudo microceph.ceph -s
    return 0
  fi

  echo "Timed out waiting for Ceph health to become HEALTH_OK on ${unit}"
  dump_microceph_cluster_diagnostics "$app" "$unit"
  exit 1
}

function dump_vm_diagnostics() {
  local vm="${1?missing}"

  echo "--- diagnostics for ${vm} ---"
  lxc info "$vm" || true
  lxc list "$vm" || true
  lxc exec "$vm" -- cloud-init status || true
  lxc exec "$vm" -- ip -4 addr show || true
  lxc exec "$vm" -- ls -la /home || true
  lxc exec "$vm" -- ls -la /home/ubuntu || true
  lxc exec "$vm" -- ls -la /home/ubuntu/.ssh || true
  lxc exec "$vm" -- journalctl -u cloud-init --no-pager -n 200 || true
}

function wait_for_vm_running() {
  local vm="${1?missing}"
  local timeout="${2?missing}"
  local start=$SECONDS

  while :; do
    if lxc list "$vm" --format=json | jq -e '.[0].state.status == "Running"' > /dev/null 2>&1; then
      return 0
    fi

    if (( SECONDS - start >= timeout )); then
      echo "timeout waiting for ${vm} to reach Running state"
      dump_vm_diagnostics "$vm"
      return 1
    fi
    sleep 5
  done
}

function wait_for_vm_exec_ready() {
  local vm="${1?missing}"
  local timeout="${2?missing}"
  local start=$SECONDS

  until lxc exec "$vm" -- true &>/dev/null; do
    if (( SECONDS - start >= timeout )); then
      echo "timeout waiting for agent on ${vm}"
      dump_vm_diagnostics "$vm"
      return 1
    fi
    sleep 5
  done
}

function wait_for_vm_cloud_init() {
  local vm="${1?missing}"
  local timeout="${2?missing}"
  local start=$SECONDS
  local elapsed remaining rc

  while :; do
    elapsed=$((SECONDS - start))
    remaining=$((timeout - elapsed))
    if (( remaining <= 0 )); then
      echo "timeout waiting for cloud-init on ${vm}"
      dump_vm_diagnostics "$vm"
      return 1
    fi

    if timeout "${remaining}s" lxc exec "$vm" -- cloud-init status --wait &>/dev/null; then
      return 0
    fi

    rc=$?
    if (( rc == 124 )); then
      echo "timeout waiting for cloud-init on ${vm}"
      dump_vm_diagnostics "$vm"
      return 1
    fi

    sleep 5
  done
}

function wait_for_vm_path() {
  local vm="${1?missing}"
  local path="${2?missing}"
  local timeout="${3?missing}"
  local start=$SECONDS

  while :; do
    if lxc exec "$vm" -- test -d "$path" &>/dev/null; then
      return 0
    fi

    if (( SECONDS - start >= timeout )); then
      echo "timeout waiting for path ${path} on ${vm}"
      dump_vm_diagnostics "$vm"
      return 1
    fi
    sleep 5
  done
}

function wait_for_vm_ipv4_in_subnet() {
  local vm="${1?missing}"
  local subnet_prefix="${2?missing}"
  local timeout="${3?missing}"
  local start=$SECONDS

  while :; do
    if lxc list "$vm" --format=json | jq -e --arg prefix "$subnet_prefix" '
      .[0].state.network[]?.addresses[]?
      | select(.family == "inet" and (.address | startswith($prefix)))
    ' > /dev/null 2>&1; then
      return 0
    fi

    if (( SECONDS - start >= timeout )); then
      echo "timeout waiting for ${vm} to acquire IPv4 in ${subnet_prefix}0/24"
      dump_vm_diagnostics "$vm"
      return 1
    fi
    sleep 5
  done
}

function wait_for_vms() {
  local timeout="${1?missing}"
  shift
  local vms=("$@")

  if [[ ${#vms[@]} -eq 0 ]]; then
    echo "wait_for_vms requires at least one VM name"
    return 1
  fi

  for vm in "${vms[@]}"; do
    wait_for_vm_running "$vm" "$timeout" || return 1
    wait_for_vm_exec_ready "$vm" "$timeout" || return 1
    wait_for_vm_cloud_init "$vm" "$timeout" || return 1
    wait_for_vm_path "$vm" /home/ubuntu "$timeout" || return 1
    wait_for_vm_ipv4_in_subnet "$vm" "10.85.4." "$timeout" || return 1
    lxc exec "$vm" -- install -d -m 700 /home/ubuntu/.ssh
  done

  echo "All requested VMs are running with cloud-init complete and cluster IPs assigned."
}

function vm_cluster_ip() {
  local vm="${1?missing}"
  lxc list "$vm" --format=json | jq -r '
    .[0].state.network[]?.addresses[]?
    | select(.family == "inet" and (.address | startswith("10.85.4.")))
    | .address
  ' | head -n1
}

function prepare_3_vms() {
  set -euxo pipefail
  local vms=(node01 node02 node03)

  for vm in "${vms[@]}"; do
    lxc launch --vm ubuntu:24.04 "$vm" -c limits.memory=8GB -c limits.cpu=4 -d root,size=25GB
  done

  wait_for_vms 300 "${vms[@]}"
  lxc ls

  ssh-keygen -t rsa -N "" -f ./lxdkey
  for vm in "${vms[@]}"; do
    lxc exec "$vm" -- install -d -m 700 /home/ubuntu/.ssh
    lxc file push ./lxdkey ./lxdkey.pub "$vm"/home/ubuntu/.ssh/
    lxc exec "$vm" -- sh -c "cat /home/ubuntu/.ssh/lxdkey.pub >> /home/ubuntu/.ssh/authorized_keys"
    lxc exec "$vm" -- chmod 600 /home/ubuntu/.ssh/authorized_keys
    lxc exec "$vm" -- sh -c "cat /home/ubuntu/.ssh/authorized_keys"
  done

  stat -c "%a %n" ./tests/scripts/assets/*
  for vm in "${vms[@]}"; do
    vm_ip=$(vm_cluster_ip "$vm")
    if [[ -z "$vm_ip" ]]; then
      echo "Did not find cluster IP for ${vm}"
      dump_vm_diagnostics "$vm"
      return 1
    fi
    ssh -o "StrictHostKeyChecking=no" "ubuntu@$vm_ip" -i ./lxdkey -f ls
    juju add-machine "ssh:ubuntu@$vm_ip" --private-key ./lxdkey --public-key ./lxdkey.pub
    sleep 10s
  done
}

function juju_crashdump() {
    local model=${1:-microceph-test}
    if ! snap list | grep -q "^juju-crashdump"; then
        sudo snap install --classic juju-crashdump
    fi
    juju-crashdump \
      -m "$model" \
      -o logs \
      -a juju-show-unit \
      -a juju-show-unit \
      -a juju-show-status-log \
      -a sosreport \
      --as-root \
      -j '*' \
      /var/snap/microceph/
}

function sanitize_model_name() {
    local model="${1?missing}"
    local model_="${model//\//-}"
    echo "${model_//:/-}"
}

function model_has_microceph_app() {
    local model="${1?missing}"
    juju status -m "$model" --format json | jq -e '.applications.microceph != null' > /dev/null 2>&1
}

function resolve_target_models() {
    local explicit_model="${1:-}"
    local discovered_models
    local model
    local models=()

    if [[ -n "$explicit_model" ]]; then
      printf "%s\n" "$explicit_model"
      return
    fi

    if discovered_models=$(juju models --format json | jq -r '.models[].name'); then
      while read -r model; do
        [[ -z "$model" ]] && continue
        case "$model" in
          controller|*/controller)
            continue
            ;;
        esac
        models+=("$model")
      done <<< "$discovered_models"
    else
      echo "Unable to discover models via 'juju models'; falling back to microceph-test" >&2
      models=("microceph-test")
    fi

    if [[ ${#models[@]} -eq 0 ]]; then
      echo "No non-controller models found; falling back to microceph-test" >&2
      models=("microceph-test")
    fi

    printf "%s\n" "${models[@]}"
}

function collect_juju_logs_for_model() {
    local model="${1?missing}"
    local model_
    model_=$(sanitize_model_name "$model")

    echo "Collecting logs for model: $model"
    juju status -m "$model" -o "logs/${model_}.yaml" || {
      echo "Not able to get status for model $model"
      return 1
    }
    cat "logs/${model_}.yaml"

    juju debug-log -m "$model" --replay --no-tail --limit 5000 &> "logs/${model_}-debug-log.txt" \
      || echo "Not able to get debug logs for model $model"
}

function collect_microceph_specific_logs_for_model() {
    local model="${1?missing}"
    local model_
    model_=$(sanitize_model_name "$model")

    if ! model_has_microceph_app "$model"; then
      echo "Model $model has no microceph app; skipping microceph-specific collection"
      return 1
    fi

    juju ssh -m "$model" microceph/leader sudo microceph status &> "logs/${model_}-microceph-status.txt" || true
    juju ssh -m "$model" microceph/leader sudo microceph.ceph status &> "logs/${model_}-ceph-status.txt" || true

    juju_crashdump "$model" || echo "Not able to collect crashdump for model $model"
}

function collect_microceph_logs_for_model() {
    local model="${1?missing}"
    local model_
    model_=$(sanitize_model_name "$model")

    collect_juju_logs_for_model "$model" || return 0

    if collect_microceph_specific_logs_for_model "$model"; then
      cat "logs/${model_}-microceph-status.txt"
    fi
}

# Collect Juju/MicroCeph diagnostics.
#
# Usage:
#   collect_microceph_logs <model>
#     - collect only from the explicitly provided model.
#   collect_microceph_logs
#     - auto-discover all non-controller models on the current controller and
#       collect diagnostics from each.
function collect_microceph_logs() {
    mkdir -p logs
    local explicit_model="${1:-}"
    local model
    local models=()

    if [[ -n "$explicit_model" ]]; then
      collect_microceph_logs_for_model "$explicit_model"
      return
    fi

    mapfile -t models < <(resolve_target_models)
    printf "%s\n" "${models[@]}" > logs/models-collected.txt

    for model in "${models[@]}"; do
      collect_microceph_logs_for_model "$model"
    done
}

function collect_k8s_pod_logs_for_model() {
    local model="${1?missing}"
    local kubectl="${2:-microk8s.kubectl}"
    local model_ name pods pod

    model_=$(sanitize_model_name "$model")
    name="${model##*/}"

    if command -v "$kubectl" > /dev/null 2>&1; then
      if sudo "$kubectl" get namespace "$name" > /dev/null 2>&1; then
        pods=$(sudo "$kubectl" get pods -n "$name" -o=jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
        for pod in $pods; do
          sudo "$kubectl" logs --ignore-errors -n "$name" --all-containers "$pod" \
            &> "logs/${model_}-${pod}.log" || echo "Not able to get log for pod $pod"
        done
      else
        echo "Namespace $name not found; skipping pod log collection"
      fi
    else
      echo "$kubectl command not found; skipping pod log collection"
    fi
}

# Collect Sunbeam + MicroCeph diagnostics.
#
# Usage:
#   collect_sunbeam_and_microceph_logs <model>
#     - collect only from the explicitly provided model.
#   collect_sunbeam_and_microceph_logs
#     - auto-discover all non-controller models on the current controller and
#       collect diagnostics from each.
#
# Notes:
#   - Kubernetes pod logs are collected from a namespace matching the Juju model
#     name (suffix after '/'), if that namespace exists.
#   - MicroCeph-specific commands and crashdump are only run when a microceph
#     application exists in the model.
function collect_sunbeam_and_microceph_logs() {
    mkdir -p logs
    local kubectl="microk8s.kubectl"
    local explicit_model="${1:-}"
    local model
    local models=()

    if compgen -G "$HOME/snap/openstack/common/logs/*.log" > /dev/null; then
      cp -rf "$HOME"/snap/openstack/common/logs/*.log logs/ || echo "Not able to copy sunbeam service logs"
    else
      echo "No sunbeam service logs found at $HOME/snap/openstack/common/logs/*.log"
    fi

    mapfile -t models < <(resolve_target_models "$explicit_model")
    printf "%s\n" "${models[@]}" > logs/models-collected.txt

    for model in "${models[@]}"; do
      collect_juju_logs_for_model "$model" || continue
      collect_k8s_pod_logs_for_model "$model" "$kubectl"
      collect_microceph_specific_logs_for_model "$model" || true
    done
}

run="${1}"
shift

$run "$@"
