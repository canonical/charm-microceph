locals {
  user_data      = data.external.user_data.result
  access_key_key = user_data["keys"][0]["user"]
  secrets_key    = user_data["keys"][0]["access_key"]

  endpoint       = data.external.s3_endpoints.result["endpoint"]

}