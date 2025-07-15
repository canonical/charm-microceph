locals {
  endpoint_bindings = toset([for endpoint, space in var.networks :
    {
      "space"    = "${space}"
      "endpoint" = "${endpoint}"
    }
  ])
}