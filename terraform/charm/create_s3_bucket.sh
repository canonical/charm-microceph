#!/bin/bash

BUCKET=$1

cat > ~/.s3cfg <<EOF
[default]
access_key = ${S3_ACCESS_KEY}
secret_key = ${S3_SECRET_KEY}
host_base = ${ENDPOINT}
host_bucket = ubuntu/%(bucket)
check_ssl_certificate = False
check_ssl_hostname = False
use_https = False
EOF


chmod 0600 ~/.s3cfg

s3cmd mb -P s3://"${BUCKET}"