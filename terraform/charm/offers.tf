resource "juju_offer" "ceph" {
  model            = var.model
  application_name = "microceph"
  endpoints         = ["ceph", "radosgw", "mds"]
}
