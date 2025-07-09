## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_juju"></a> [juju](#requirement\_juju) | 0.20.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_juju"></a> [juju](#provider\_juju) | 0.20.0 |
| <a name="provider_null"></a> [null](#provider\_null) | n/a |

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_microceph"></a> [microceph](#module\_microceph) | ../charm | n/a |

## Resources

| Name | Type |
|------|------|
| [null_resource.deployment_time](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [juju_model.model](https://registry.terraform.io/providers/juju/juju/0.20.0/docs/data-sources/model) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_microceph"></a> [microceph](#input\_microceph) | configuration for the microceph deployment | <pre>object({<br/>    app_name    = optional(string)<br/>    base        = string<br/>    channel     = string<br/>    config      = optional(map(string))<br/>    constraints = optional(string)<br/>    resources   = optional(map(string))<br/>    revision    = optional(string)<br/>    storage     = optional(map(string))<br/>    units       = number<br/>    endpoint_bindings = optional(set(object({<br/>      space    = string<br/>      endpoint = optional(string)<br/>    })))<br/><br/>  })</pre> | <pre>{<br/>  "base": "24.04",<br/>  "channel": "squid/stable",<br/>  "units": 3<br/>}</pre> | no |
| <a name="input_model_name"></a> [model\_name](#input\_model\_name) | Reference to a `juju_model`. | `string` | n/a | yes |
| <a name="input_osd_disks"></a> [osd\_disks](#input\_osd\_disks) | n/a | <pre>object({<br/>    path      = string<br/>    loop_spec = optional(string)<br/>  })</pre> | `null` | no |
| <a name="input_radosgw_user"></a> [radosgw\_user](#input\_radosgw\_user) | details of the user to create for radosgw | <pre>object({<br/>    user_id      = string<br/>    display_name = string<br/>  })</pre> | `null` | no |
| <a name="input_s3_buckets"></a> [s3\_buckets](#input\_s3\_buckets) | list of s3 buckets to create | `set(string)` | `[]` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_charms"></a> [charms](#output\_charms) | n/a |
| <a name="output_metadata"></a> [metadata](#output\_metadata) | n/a |
| <a name="output_model_info"></a> [model\_info](#output\_model\_info) | n/a |
| <a name="output_provides"></a> [provides](#output\_provides) | n/a |
