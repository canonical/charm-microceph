terraform {
  required_version = ">= 1.5"

  required_providers {
    juju = {
      source  = "juju/juju"
      version = "~> 2.1.0"
    }
  }
}
