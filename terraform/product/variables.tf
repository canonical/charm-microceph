variable "model_name" {
  description = "Reference to a `juju_model`."
  type        = string
}


variable "s3_buckets" {
  description = "list of s3 buckets to create"
  type        = set(string)
  default     = []
}

varaible "radosgw_user" {
  description = "details of the user to create for radosgw"
  type        = object({
    user_id   = string
    display_name = string
  })

  default = {}
}

variable "osd_disks" {
  type = object({
    path      = string
    loop_spec = string
  })

  default = null
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
    endpoint_bindings = optional(set(object({
      space    = string
      endpoint = optional(string)
    })))

  })

  default = {
    base    = "24.04"
    channel = "squid/stable"
    units   = 3
  }
}