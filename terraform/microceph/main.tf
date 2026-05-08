locals {
  # decide if we need too lookup the model uuid
  use_model_lookup = var.model_uuid == null && var.model_name != null && var.model_owner != null
}

data "juju_model" "target" {
  count = local.use_model_lookup ? 1 : 0
  name  = var.model_name
  owner = var.model_owner
}

locals {
  resolved_model_uuid = coalesce(
    var.model_uuid,
    try(data.juju_model.target[0].uuid, null)
  )

  config_defaults = {
    "snap-channel"        = "tentacle/edge"
    "default-pool-size"   = "3"
    "enable-rgw"          = ""
    "region"              = "RegionOne"
    "namespace-projects"  = "false"
    "rbd-stats-pools"     = ""
    "enable-perf-metrics" = "false"
  }

  rendered_config = merge(local.config_defaults, var.config)
}

resource "juju_application" "microceph" {
  name       = var.app_name
  model_uuid = local.resolved_model_uuid
  units      = var.units
  trust      = true

  lifecycle {
    precondition {
      condition     = local.resolved_model_uuid != null
      error_message = "Unable to determine model UUID. Check model_name/model_owner or provide model_uuid."
    }
  }

  charm {
    name     = "microceph"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config = local.rendered_config

  storage_directives = var.storage_directives
}
