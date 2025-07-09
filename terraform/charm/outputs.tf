output "app_name" {
  value = var.app_name
}

output "provides" {
  value = {
    microceph_endpoints = juju_offer.ceph
    s3_endpoints        = data.external.s3_endpoints.result
  }
}
