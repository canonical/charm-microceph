variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "microceph"
}

variable "base" {
  description = "Ubuntu bases to deploy the charm onto"
  type        = string
  default     = "ubuntu@24.04"
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
  description = "Ednpoint bindings for juju spaces"
  type = set(object({
    space    = string
    endpoint = optional(string, null)
  }, {}))
}

variable "model" {
  description = "Reference to a `juju_model`."
  type        = string
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