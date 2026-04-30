variable "app_name" {
  description = "Name to give the deployed application"
  type        = string
  default     = "microceph"
}

variable "model_uuid" {
  description = "UUID of the model to deploy to. Provide either this or both model_name and model_owner."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.model_uuid != null && trimspace(var.model_uuid) != ""
      ) || (
      var.model_uuid == null &&
      var.model_name != null && trimspace(var.model_name) != "" &&
      var.model_owner != null && trimspace(var.model_owner) != ""
    )
    error_message = "Provide either model_uuid or both model_name and model_owner."
  }
}

variable "model_name" {
  description = "Name of the model to deploy to. Requires model_owner when model_uuid is not set."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.model_name == null || trimspace(var.model_name) != ""
    error_message = "model_name must not be an empty string when provided."
  }
}

variable "model_owner" {
  description = "Owner of the model to deploy to. Requires model_name when model_uuid is not set."
  type        = string
  default     = "admin"
  nullable    = true

  validation {
    condition     = var.model_owner == null || trimspace(var.model_owner) != ""
    error_message = "model_owner must not be an empty string."
  }
}

variable "units" {
  description = "Unit count/scale"
  type        = number
  default     = 1

  # 0 units --> only application deployed
  validation {
    condition     = var.units >= 0 && floor(var.units) == var.units
    error_message = "units must be an integer greater than or equal to 0."
  }
}

variable "config" {
  description = <<-DESC
    Additional charm configuration as key/value pairs. Keys must match MicroCeph charm config
    options (for example: snap-channel, default-pool-size, enable-rgw, region, namespace-projects,
    rbd-stats-pools, enable-perf-metrics). Values are rendered as strings and forwarded to Juju.
  DESC
  type        = map(string)
  default     = {}

  validation {
    condition = length([
      for key in keys(var.config) : key
      if !contains(
        [
          "snap-channel",
          "default-pool-size",
          "enable-rgw",
          "region",
          "namespace-projects",
          "rbd-stats-pools",
          "enable-perf-metrics"
        ],
        key
      )
    ]) == 0
    error_message = "Unsupported config key."
  }
}

variable "storage_directives" {
  description = "Map of storage used by the application"
  type        = map(string)
  default     = {}
}

variable "channel" {
  description = "Channel that the microceph charm is deployed from"
  type        = string
  default     = "tentacle/edge"
}

variable "revision" {
  description = "Revision number of the charm"
  type        = number
  nullable    = true
  default     = null
}

variable "base" {
  description = "Base to deploy the microceph charm with"
  type        = string
  default     = "ubuntu@24.04"
}
