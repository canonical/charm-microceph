resource "juju_offer" "ceph" {
  model            = var.model
  application_name = "microceph"
  endpoint         = "ceph"
}

resource "juju_offer" "radosgw" {
  model            = var.model
  application_name = "microceph"
  endpoint         = "radosgw"
}

resource "juju_offer" "mds" {
  model            = var.model
  application_name = "microceph"
  endpoint         = "mds"
}