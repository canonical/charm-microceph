# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "microceph" {
  name  = var.app_name
  model = var.model

  charm {
    name     = "microceph"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config             = var.config
  constraints        = var.constraints
  units              = var.units
  resources          = var.resources
  storage_directives = var.storage
}

resource "null_resource" "juju_wait" {
  depends_on = [juju_application.microceph]

  command = "juju wait-for model ${var.model} --query='forEach(units, unit => unit.workload-status==\"active\")' --timeout 60m --summary"
}

data "external" "s3_endpoints" {
  depends_on = [null_resource.juju_wait]
}