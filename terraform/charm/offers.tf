resource "juju_offer" "ceph" {
  model            = var.model
  application_name = "microceph"
  endpoint         = ["ceph","radosgw","mds"]
}
