output "charm_name" {
  value = var.app_name
}

output "microceph_endpoints" {
  value = juju_offer.ceph
}

#TODO FIGURE OUT HOW TO DO THIS
#output "s3_endpoints" {
#  value = data.external.s3_endpoints
#}
