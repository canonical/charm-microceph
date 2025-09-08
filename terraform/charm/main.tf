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
    command = "juju wait-for model ${var.model} --query='forEach(units, unit => unit.workload-status==\"active\")' --timeout 60m --summary; juju switch ${var.model}"
  }
}

resource "null_resource" "add_osds" {
  depends_on = [null_resource.juju_wait]
  provisioner "local-exec" {
    command = "${path.module}/add_osds.py ${var.osd_disks.path} ${var.osd_disks.loop_spec}"
  }
}


resource "null_resource" "install_s3cmd" {
  depends_on = [null_resource.add_osds]
  provisioner "local-exec" {
    command = "${path.module}/install_s3cmd.sh"
  }

  triggers = {
    always_run = "deploy_once"
  }
}
data "external" "s3_endpoints" {
  depends_on = [null_resource.add_osds]
  program    = ["${path.module}/get_s3_endpoints.sh", var.app_name, var.model]
}

data "external" "radosgw_user" {
  depends_on = [null_resource.add_osds]
  program    = ["bash", "${path.module}/create_radosgw_user.sh", var.radosgw_user.user_id, var.radosgw_user.display_name, var.model]
}


resource "null_resource" "s3_buckets" {
  for_each   = var.s3_buckets
  depends_on = [data.external.radosgw_user]

  provisioner "local-exec" {
    command = "${path.module}/create_s3_bucket.sh ${each.value}"
    environment = {
      S3_ACCESS_KEY = local.access_key
      S3_SECRET_KEY = local.secret_key
      ENDPOINT      = local.endpoint
    }
  }

}


data "local_sensitive_file" "ssh_key" {
  filename = var.ssh_key
  count    = length(var.ssh_key) > 0 ? 1 : 0
}

resource "juju_ssh_key" "mykey" {
  count   = length(var.ssh_key) > 0 ? 1 : 0
  model   = var.model
  payload = data.local_sensitive_file.ssh_key[0].content
}
