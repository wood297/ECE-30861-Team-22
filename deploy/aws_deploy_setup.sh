#!/usr/bin/env bash
# One-time AWS setup for automated deployment (GETTING_STARTED.md §7.2). Safe to re-run. Needs
# AWS CLI v2, signed in (`aws login`).
#
# Creates: a private S3 bucket for release tarballs, and the IAM user GitHub Actions deploys as.
# We'd use GitHub's OIDC sign-in instead of a stored key, but our AWS organization's service
# control policy denies iam:CreateOpenIDConnectProvider. This script doesn't create the access
# key, so the secret never appears in its output: see GETTING_STARTED.md §8.1.
set -euo pipefail

REGION="${REGION:-us-east-2}"
NAME="${NAME:-team22-cra}"
INSTANCE_ID="${INSTANCE_ID:-i-09eef96d3f2df685a}"

aws() { command aws --region "$REGION" --output text "$@"; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account)"
BUCKET="$NAME-deploy-$ACCOUNT_ID"
USER_NAME="$NAME-deploy"

# 1. Release bucket: private, and old releases delete themselves
if ! aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-releases","Status":"Enabled","Filter":{"Prefix":"releases/"},"Expiration":{"Days":30}}]}' >/dev/null
echo "release bucket: $BUCKET"

# 2. Deploy user: can only upload releases to this bucket and run commands on this instance
if ! aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  aws iam create-user --user-name "$USER_NAME" >/dev/null
fi
aws iam put-user-policy --user-name "$USER_NAME" --policy-name deploy --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {\"Effect\": \"Allow\", \"Action\": [\"s3:PutObject\", \"s3:GetObject\"], \"Resource\": \"arn:aws:s3:::$BUCKET/releases/*\"},
    {\"Effect\": \"Allow\", \"Action\": \"ssm:SendCommand\", \"Resource\": [
      \"arn:aws:ec2:$REGION:$ACCOUNT_ID:instance/$INSTANCE_ID\",
      \"arn:aws:ssm:$REGION::document/AWS-RunShellScript\"
    ]},
    {\"Effect\": \"Allow\", \"Action\": \"ssm:GetCommandInvocation\", \"Resource\": \"*\"}
  ]
}"
echo "deploy user:    $USER_NAME"
