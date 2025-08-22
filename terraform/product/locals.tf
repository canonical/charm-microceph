locals {

  bindings = {
    "management" : ["admin", "peers"]
    "storage" : ["ceph", "public", "mds", "radosgw"]
    "storage_cluster" : ["cluster"]
  }
  endpoint_bindings = toset(
    flatten(
      [for network, space in var.networks :
        [for endpoint in local.bindings[network] :
          {
            "space"    = space
            "endpoint" = endpoint
        }]
    if space != null])
  )
}