
module "microceph" {
  source            = "../charm"
  app_name          = var.microceph.app_name
  base              = var.microceph.base
  channel           = var.microceph.channel
  config            = var.microceph.config
  constraints       = var.microceph.constraints
  endpoint_bindings = var.microceph.endpoint_bindings
  revision          = var.microceph.revision
  storage           = var.microceph.storage
  units             = var.microceph.units
  model             = var.model_name
}

resource "null_resource" "deployment_time" {
  triggers = {
    timestamp   = timestamp()
    ceph_config = jsonencode(var.microceph)
  }

  lifecycle {
    ignore_changes = [triggers.timestamp]
  }
}