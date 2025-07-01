variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "microceph"
}

variable "base" {
  description = "Ubuntu bases to deploy the charm onto"
  type        = string
}

variable "channel" {
  description = "The channel to use when deploying a charm."
  type        = string
  default     = "squid/beta"
}

variable "config" {
  description = "Application config. Details about available options can be found at https://charmhub.io/ceph-radosgw/configurations."
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = "arch=amd64"
}

variable "endpoint_bindings" {
  description = "Endpoint bindings for juju spaces"
  type = optional(set(object({
    space    = string
    endpoint = optional(string)
  })))
  default = []
}

variable "model" {
  description = "Reference to a `juju_model`."
  type        = string
}

variable "machines" {
  description = "List of juju_machine resources to use for deployment"
  type        = set(string)
  default     = []
}

variable "resources" {
  description = "Resources to use with the application."
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Revision number of the charm"
  type        = number
  default     = null
}

variable "storage" {
  description = "Storage configuration for this application."
  type        = map(string)
  default     = {}
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}