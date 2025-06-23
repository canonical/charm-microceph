resource "juju_offer" "ceph" {
  depends_on       = [null_resource.juju_wait]
  model            = var.model
  application_name = var.app_name
  endpoints        = ["ceph", "radosgw", "mds"]
}
