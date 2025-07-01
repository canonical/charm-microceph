output "charms" {
  value = {
    microceph = var.microceph
  }
}

output "provides" {
  value = {
    sass = module.microceph.juju_offer.ceph
  }
}

output "model_info" {
  valude = {
    model_name = var.model_name
  }
}

output "metadata" {
  value = {
    version     = "0.1.0"
    deployed_at = formatdate("YYYY-MM-DD'T'hh:mm:ssZ", null_resource.deployment_time.triggers.timestamp)
    updated_at  = formatdate("YYYY-MM-DD'T'hh:mm:ssZ", timestamp())
  }
}