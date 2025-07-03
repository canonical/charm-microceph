locals {
  radosgw_user = data.external.radosgw_user.result
  access_key   = jsondecode(local.radosgw_user["keys"])[0]["access_key"]
  secret_key   = jsondecode(local.radosgw_user["keys"])[0]["secret_key"]
  endpoint     = data.external.s3_endpoints.result["endpoint"]
}
