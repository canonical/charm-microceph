# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import subprocess

import yaml


# These functions are picked from traefik-k8s-operator repo.
# Raised a bug to move these functions to common repo so that
# any charm can reuse them.
# https://github.com/canonical/traefik-k8s-operator/issues/175
def get_unit_info(unit: str, model: str) -> dict:
    """Returns unit-info data structure.

    Example:
    cephclient/6:
      machine: "26"
      opened-ports: []
      public-address: 10.121.193.146
      charm: local:jammy/cephclient-requirer-mock-11
      leader: true
      life: alive
      relation-info:
      - relation-id: 9
        endpoint: ceph
        related-endpoint: ceph
        application-data: {}
        related-units:
          microceph/20:
            in-scope: true
            data:
              auth: cephx
              broker-rsp-cephclient-6: '{"exit-code": 0, "request-id": "2ab0acf4"}'
              ceph-public-address: 10.121.193.17
              egress-subnets: 10.121.193.17/32
              ingress-address: 10.121.193.17
              key: AQDtJVtk30rAFhAAw+MRUxjSVe+BJmlX6HXMZg==
              private-address: 10.121.193.17
    """
    cmd = ["juju", "show-unit", "-m", model, unit]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    raw_data = proc.stdout.read().decode("utf-8").strip()

    data = yaml.safe_load(raw_data) if raw_data else None

    if not data:
        raise ValueError(f"No Unit info available for {unit}")

    if unit not in data:
        raise KeyError(unit, f"not in {data!r}")

    unit_data = data[unit]
    return unit_data


def get_relation_data(unit: str, endpoint: str, related_unit: str, model: str) -> dict:
    """Returns relation data for a specific endpoint and related unit.

    :param unit: Unit name for which unit-info to be extracted
    :param endpoint: Endpoint name on local unit
    :param related_unit: Remote unit name
    :param model: Model name
    """
    unit_data = get_unit_info(unit, model)
    for relation in unit_data.get("relation-info", []):
        related_units = relation.get("related-units", {})
        if endpoint == relation.get("endpoint") and related_unit in related_units:
            return related_units.get(related_unit).get("data")

    return {}
