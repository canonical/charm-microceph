locals {
  radosgw_user      = data.external.radosgw_user.result
  access_key_key = local.radosgw_user["keys"][0]["user"]
  secrets_key    = local.radosgw_user["keys"][0]["access_key"]

  endpoint = data.external.s3_endpoints.result["endpoint"]

}