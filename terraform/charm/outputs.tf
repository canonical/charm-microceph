output "charm_name" {
  value = var.charm_name
}

output "s3_endpoints" {
  value = data.external.s3_endpoints
}
