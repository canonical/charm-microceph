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
  provisioner "local-exec" {
    command = "juju wait-for model ${var.model} --query='forEach(units, unit => unit.workload-status==\"active\")' --timeout 60m --summary"
  }
}

resource "null_resource" "add_osds" {
  depends_on = [null_resource.juju_wait]
  provisioner "local-exec" {
    command = "./add_osds"
  }
}


data "external" "s3_endpoints" {
  depends_on = [null_resource.juju_wait]
  program    = ["./get_s3_endpoints.sh"]
}