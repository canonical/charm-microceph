terraform {
  required_providers {
    juju = {
      source  = "registry.terraform.io/juju/juju" # (uses local provider repository)
      version = "0.20.0"
    }
  }
}