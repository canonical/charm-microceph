output "app_name" {
  value = var.app_name
}

output "provides" {
  value = {
    microceph_endpoints = juju_offer.ceph
    s3 = {
      endpoint   = data.external.s3_endpoints.result.endpoint
      access_key = local.access_key
      secret_key = local.secret_key
    }
  }
}
