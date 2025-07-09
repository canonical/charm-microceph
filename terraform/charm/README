## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_juju"></a> [juju](#requirement\_juju) | 0.20.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_external"></a> [external](#provider\_external) | n/a |
| <a name="provider_juju"></a> [juju](#provider\_juju) | 0.20.0 |
| <a name="provider_null"></a> [null](#provider\_null) | n/a |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| juju_application.microceph | resource |
| juju_offer.ceph | resource |
| [null_resource.add_osds](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [null_resource.install_s3cmd](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [null_resource.juju_wait](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [null_resource.s3_buckets](https://registry.terraform.io/providers/hashicorp/null/latest/docs/resources/resource) | resource |
| [external_external.radosgw_user](https://registry.terraform.io/providers/hashicorp/external/latest/docs/data-sources/external) | data source |
| [external_external.s3_endpoints](https://registry.terraform.io/providers/hashicorp/external/latest/docs/data-sources/external) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_app_name"></a> [app\_name](#input\_app\_name) | Name of the application in the Juju model. | `string` | `"microceph"` | no |
| <a name="input_base"></a> [base](#input\_base) | Ubuntu bases to deploy the charm onto | `string` | n/a | yes |
| <a name="input_channel"></a> [channel](#input\_channel) | The channel to use when deploying a charm. | `string` | `"squid/beta"` | no |
| <a name="input_config"></a> [config](#input\_config) | Application config. Details about available options can be found at https://charmhub.io/ceph-radosgw/configurations. | `map(string)` | `{}` | no |
| <a name="input_constraints"></a> [constraints](#input\_constraints) | Juju constraints to apply for this application. | `string` | `"arch=amd64"` | no |
| <a name="input_endpoint_bindings"></a> [endpoint\_bindings](#input\_endpoint\_bindings) | Endpoint bindings for juju spaces | <pre>set(object({<br/>    space    = string<br/>    endpoint = optional(string)<br/>  }))</pre> | `[]` | no |
| <a name="input_machines"></a> [machines](#input\_machines) | List of juju\_machine resources to use for deployment | `set(string)` | `[]` | no |
| <a name="input_model"></a> [model](#input\_model) | Reference to a `juju_model`. | `string` | n/a | yes |
| <a name="input_osd_disks"></a> [osd\_disks](#input\_osd\_disks) | n/a | <pre>object({<br/>    path      = string<br/>    loop_spec = optional(string)<br/>  })</pre> | `null` | no |
| <a name="input_radosgw_user"></a> [radosgw\_user](#input\_radosgw\_user) | Name of the radosgw user | <pre>object({<br/>    user_id      = string<br/>    display_name = string<br/>  })</pre> | n/a | yes |
| <a name="input_resources"></a> [resources](#input\_resources) | Resources to use with the application. | `map(string)` | `{}` | no |
| <a name="input_revision"></a> [revision](#input\_revision) | Revision number of the charm | `number` | `null` | no |
| <a name="input_s3_buckets"></a> [s3\_buckets](#input\_s3\_buckets) | set of bucket names to create | `set(string)` | `[]` | no |
| <a name="input_storage"></a> [storage](#input\_storage) | Storage configuration for this application. | `map(string)` | `{}` | no |
| <a name="input_units"></a> [units](#input\_units) | Number of units to deploy | `number` | `1` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_app_name"></a> [app\_name](#output\_app\_name) | n/a |
| <a name="output_provides"></a> [provides](#output\_provides) | n/a |
