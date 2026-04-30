# MicroCeph Terraform module

This module deploys the `microceph` Juju application using the
[Terraform Juju
provider](https://registry.terraform.io/providers/juju/juju/latest/docs).

The terraform module will need an existing Juju model to deploy into.
It supports either supplying the model UUID directly or looking it up
via the model name.

## Usage

Provide either the model UUID or the model name/owner pair when applying the module:

```bash
terraform init
terraform apply -var="model_uuid=<MODEL_UUID>"
```

Or, alternatively: 

```bash
terraform init
terraform apply -var="model_name=<MODEL_NAME>" -var="model_owner=<MODEL_OWNER>"
```


### Using the Terragrunt wrapper

The provided `terragrunt.hcl` passes environment-derived defaults into the
module, making it convenient to switch between models without editing variable
files:

- `JUJU_MODEL` controls `model_name` (and triggers `model_owner=admin`).
- `MICROCEPH_MODEL_OWNER` optionally overrides the owner (default "admin").
- `MICROCEPH_MODEL_UUID` skips the name lookup entirely.

Invoke it from the repository root with:

```bash
terragrunt init
JUJU_MODEL=my-model terragrunt apply
```

You can still override inputs on the command line using the `-var` flag (for example
`-var='units=6'`) or via a `terraform.tfvars` file in the module directory;
Terragrunt simply seeds the defaults.

Charm configuration defaults mirror `config.yaml`. You can override any of
them by supplying a `config` map variable, for example:

```bash
terragrunt apply -var='config={"snap-channel"="latest/stable","enable-rgw"="*"}'
```

Storage can be added by supplying a `storage_directive` map, for example:

```bash
terragrunt apply -var='storage_directivs={"osd-standalone"="loop,4G,1"}'
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5 |
| <a name="requirement_juju"></a> [juju](#requirement\_juju) | ~> 1.0.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_juju"></a> [juju](#provider\_juju) | 1.0.0 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [juju_application.microceph](https://registry.terraform.io/providers/juju/juju/latest/docs/resources/application) | resource |
| [juju_model.target](https://registry.terraform.io/providers/juju/juju/latest/docs/data-sources/model) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_app_name"></a> [app\_name](#input\_app\_name) | Name to give the deployed application | `string` | `"microceph"` | no |
| <a name="input_base"></a> [base](#input\_base) | Base to deploy the microceph charm with | `string` | `"ubuntu@24.04"` | no |
| <a name="input_channel"></a> [channel](#input\_channel) | Channel that the microceph charm is deployed from | `string` | `"tentacle/edge"` | no |
| <a name="input_config"></a> [config](#input\_config) | Additional charm configuration as key/value pairs. Keys must match MicroCeph charm config<br/>options (for example: snap-channel, default-pool-size, enable-rgw, region, namespace-projects,<br/>rbd-stats-pools, enable-perf-metrics). Values are rendered as strings and forwarded to Juju. | `map(string)` | `{}` | no |
| <a name="input_model_name"></a> [model\_name](#input\_model\_name) | Name of the model to deploy to. Requires model\_owner when model\_uuid is not set. | `string` | `null` | no |
| <a name="input_model_owner"></a> [model\_owner](#input\_model\_owner) | Owner of the model to deploy to. Requires model\_name when model\_uuid is not set. | `string` | `"admin"` | no |
| <a name="input_model_uuid"></a> [model\_uuid](#input\_model\_uuid) | UUID of the model to deploy to. Provide either this or both model\_name and model\_owner. | `string` | `null` | no |
| <a name="input_revision"></a> [revision](#input\_revision) | Revision number of the charm | `number` | `null` | no |
| <a name="input_storage_directives"></a> [storage\_directives](#input\_storage\_directives) | Map of storage used by the application | `map(string)` | `{}` | no |
| <a name="input_units"></a> [units](#input\_units) | Unit count/scale | `number` | `1` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_app_name"></a> [app\_name](#output\_app\_name) | The name of the deployed application |
| <a name="output_provides"></a> [provides](#output\_provides) | Map of the integration endpoints provided by the application |
| <a name="output_requires"></a> [requires](#output\_requires) | Map of the integration endpoints required by the application |
<!-- END_TF_DOCS -->
