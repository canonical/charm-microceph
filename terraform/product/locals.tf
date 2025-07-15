locals {
  endpoint_bindings = flatten([
    [for endpoint, space in var.networks :
      {
        "space"    = "${space}"
        "endpoint" = "${endpoint}"
      }
    ]
  ])
}