locals {
  radosgw_user = jsondecode(data.external.radosgw_user.result.result)
  access_key   = jsondecode(local.radosgw_user["keys"])[0]["user"]
  secret_key   = jsondecode(local.radosgw_user["keys"])[0]["access_key"]
  endpoint     = data.external.s3_endpoints.result["endpoint"]
}