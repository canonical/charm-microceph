variable "model_name" {
  description = "Reference to a `juju_model`."
  type        = string
}

variable "enable_radosgw" {
  description = "whether or not to enable radowsgw"
  type        = bool
  default     = false
}

variable "microceph" {
  description = "configuration for the microceph deployment"
  type = object({
    app_name    = optional(string)
    base        = string
    channel     = string
    config      = optional(map(string))
    constraints = optional(string)
    resources   = optional(map(string))
    revision    = optional(string)
    storage     = optional(map(string))
    units       = number
    endpoint_bindings = optional(set(string))
  })

  default = {
    base    = "24.04"
    channel = "squid/stable"
    units   = 3
  }
}