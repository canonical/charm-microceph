locals {
  endpoint_bindings = [for endpoint, space in var.networks :
    {
      "space"    = "${space}"
      "endpoint" = "${endpoint}"
    }
  ]
}