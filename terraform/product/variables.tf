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
    app_name    = optional(string)
    base        = string
    channel     = string
    config      = optional(map(string))
    constraints = optional(string)
    resources   = optional(map(string))
    revision    = optional(string)
    units       = optional(number)
    endpoint_bindings = optional(set(object({
      space    = string
      endpoint = optional(string)
    })))
  })

  default = {
    app_name          = null
    config            = {}
    constraints       = null
    resources         = {}
    revision          = null
    units             = 3
    endpoint_bindings = []
  }
}