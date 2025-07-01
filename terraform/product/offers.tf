output "charm_name" {
  value = var.app_name
}

output "microceph_endpoints" {
  value = module.microceph.juju_offer.ceph
}
