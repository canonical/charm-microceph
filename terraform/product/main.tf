
module "microceph" {
  source            = "../charm"
  app_name          = var.microceph.app_name
  base              = var.microceph.base
  channel           = var.microceph.channel
  config            = var.microceph.config
  constraints       = var.microceph.constraints
  endpoint_bindings = local.endpoint_bindings
  placement         = var.microceph.placement
  revision          = var.microceph.revision
  storage           = var.microceph.storage
  units             = var.microceph.units
  model             = var.model_name
  radosgw_user      = var.radosgw_user
  s3_buckets        = var.s3_buckets
  osd_disks         = var.osd_disks
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