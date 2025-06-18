variable "model" {
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
    app_name    = optional(string, null)
    base        = string
    channel     = string
    config      = optional(map(string), {})
    constraints = optional(string, null)
    resources   = optional(map(string), {})
    revision    = optional(string, null)
    units       = optional(number, 3)
    endpoint_bindings = optional(set(object({
      space    = string
      endpoint = optional(string, null)
    })), {})
  })
}