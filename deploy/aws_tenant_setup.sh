#!/usr/bin/env bash
# AWS setup for company accounts (Phase 5 plan, step 1.4). Safe to re-run: each step skips or
# re-applies what already exists. Needs AWS CLI v2, signed in (`aws login`).
#
#   deploy/aws_tenant_setup.sh dev     # for testing with real AWS: laptops and tenant_live_check.py
#   deploy/aws_tenant_setup.sh prod    # for the server; also changes the instance role and metadata
#
# Creates, in $REGION, for the chosen environment ($NAME and $NAME-dev):
#   - DynamoDB table $NAME-tenants[-dev]: on-demand, point-in-time recovery, TTL on `ttl` (invites)
#   - Cognito user pool $NAME-users[-dev]: email sign-up with verification, optional TOTP MFA
#   - Cognito login pages at https://$NAME[-dev].auth.$REGION.amazoncognito.com
#   - Cognito app client $NAME-web, with its secret written straight to SSM (never printed)
#   - SSM SecureString <prefix>/app/cognito-client-secret and <prefix>/app/flask-secret-key
# and, for prod only:
#   - an inline policy on $NAME-instance for the table, the /$NAME/ parameters and the pool
#   - instance metadata (IMDSv2) hop limit 1, so only processes on the server itself can reach it
#
# Prod resources have deletion protection on. Cognito pool settings are applied only when the
# pool is created, because update-user-pool resets anything it isn't given; to change them on
# an existing pool, use the console or recreate the dev pool.
set -euo pipefail

ENVIRONMENT="${1:-}"
case "$ENVIRONMENT" in
  prod) SUFFIX="" ;;
  dev)  SUFFIX="-dev" ;;
  *) echo "usage: $0 dev|prod" >&2; exit 2 ;;
esac

REGION="${REGION:-us-east-2}"
NAME="${NAME:-team22-cra}"
INSTANCE_ID="${INSTANCE_ID:-i-09eef96d3f2df685a}"
HOST="${HOST:-team22.13-58-35-224.sslip.io}"

aws() { command aws --region "$REGION" --output text "$@"; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account)"
TABLE="$NAME-tenants$SUFFIX"
POOL_NAME="$NAME-users$SUFFIX"
DOMAIN_PREFIX="$NAME$SUFFIX"
CLIENT_NAME="$NAME-web"
SECRET_PREFIX="/$NAME$SUFFIX"
if [[ "$ENVIRONMENT" == prod ]]; then
  CALLBACKS="https://$HOST/system/auth/callback"
  LOGOUTS="https://$HOST/system/"
  PROTECT=true
else
  # 5000 is Flask's default; 5050 is for Macs, where AirPlay holds 5000 (GETTING_STARTED.md §3.1)
  CALLBACKS="http://localhost:5000/auth/callback http://localhost:5050/auth/callback"
  LOGOUTS="http://localhost:5000/ http://localhost:5050/"
  PROTECT=false
fi

# 1. DynamoDB table
if ! aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --table-name "$TABLE" --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
    --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
    --tags "Key=Name,Value=$NAME" >/dev/null
fi
aws dynamodb wait table-exists --table-name "$TABLE"
if [[ "$PROTECT" == true ]]; then
  aws dynamodb update-table --table-name "$TABLE" --deletion-protection-enabled >/dev/null 2>&1 || true
fi
aws dynamodb update-continuous-backups --table-name "$TABLE" \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true >/dev/null
if [[ "$(aws dynamodb describe-time-to-live --table-name "$TABLE" --query TimeToLiveDescription.TimeToLiveStatus)" != ENABLED ]]; then
  # Fails while a previous change is still in progress; the next run finishes it
  aws dynamodb update-time-to-live --table-name "$TABLE" \
    --time-to-live-specification Enabled=true,AttributeName=ttl >/dev/null || echo "  (TTL change pending; re-run later)"
fi
echo "table:          $TABLE"

# 2. Cognito user pool
POOL_ID="$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='$POOL_NAME'].Id | [0]")"
if [[ "$POOL_ID" == "None" ]]; then
  POOL_ID="$(aws cognito-idp create-user-pool --pool-name "$POOL_NAME" \
    --username-attributes email --auto-verified-attributes email \
    --schema Name=email,Required=true,Mutable=true Name=name,Required=true,Mutable=true \
    --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false,TemporaryPasswordValidityDays=7}' \
    --mfa-configuration OFF \
    --user-attribute-update-settings AttributesRequireVerificationBeforeUpdate=email \
    --account-recovery-setting 'RecoveryMechanisms=[{Priority=1,Name=verified_email}]' \
    --admin-create-user-config AllowAdminCreateUserOnly=false \
    --deletion-protection "$([[ "$PROTECT" == true ]] && echo ACTIVE || echo INACTIVE)" \
    --user-pool-tags "Name=$NAME" \
    --query UserPool.Id)"
fi
# Optional TOTP: people can turn on an authenticator app; nobody is forced to
aws cognito-idp set-user-pool-mfa-config --user-pool-id "$POOL_ID" \
  --software-token-mfa-configuration Enabled=true --mfa-configuration OPTIONAL >/dev/null
echo "user pool:      $POOL_NAME ($POOL_ID)"

# 3. Login pages (managed login)
CURRENT_DOMAIN="$(aws cognito-idp describe-user-pool --user-pool-id "$POOL_ID" --query UserPool.Domain)"
if [[ "$CURRENT_DOMAIN" == "None" ]]; then
  aws cognito-idp create-user-pool-domain --user-pool-id "$POOL_ID" --domain "$DOMAIN_PREFIX" \
    --managed-login-version 2 >/dev/null
  CURRENT_DOMAIN="$DOMAIN_PREFIX"
fi
COGNITO_DOMAIN="$CURRENT_DOMAIN.auth.$REGION.amazoncognito.com"
echo "login pages:    https://$COGNITO_DOMAIN"

# 4. App client for the Flask pages: authorization code flow only, with a client secret
CLIENT_ID="$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --max-results 60 \
  --query "UserPoolClients[?ClientName=='$CLIENT_NAME'].ClientId | [0]")"
# Applied on create and on every re-run, so the client always matches this script
CLIENT_SETTINGS=(
  --allowed-o-auth-flows-user-pool-client --allowed-o-auth-flows code
  --allowed-o-auth-scopes openid email profile
  --supported-identity-providers COGNITO
  --callback-urls $CALLBACKS --logout-urls $LOGOUTS
  --explicit-auth-flows ALLOW_REFRESH_TOKEN_AUTH
  --prevent-user-existence-errors ENABLED --enable-token-revocation
  --access-token-validity 60 --id-token-validity 60 --refresh-token-validity 1
  --token-validity-units AccessToken=minutes,IdToken=minutes,RefreshToken=days
)
if [[ "$CLIENT_ID" == "None" ]]; then
  CLIENT_ID="$(aws cognito-idp create-user-pool-client --user-pool-id "$POOL_ID" --client-name "$CLIENT_NAME" \
    --generate-secret "${CLIENT_SETTINGS[@]}" --query UserPoolClient.ClientId)"
else
  aws cognito-idp update-user-pool-client --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" \
    --client-name "$CLIENT_NAME" "${CLIENT_SETTINGS[@]}" >/dev/null
fi
# Managed login needs a style before its pages render; Cognito's default style is enough
if ! aws cognito-idp describe-managed-login-branding-by-client --user-pool-id "$POOL_ID" \
    --client-id "$CLIENT_ID" >/dev/null 2>&1; then
  aws cognito-idp create-managed-login-branding --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" \
    --use-cognito-provided-values >/dev/null
fi
echo "app client:     $CLIENT_NAME ($CLIENT_ID)"

# 5. Secrets: passed through a private temporary file, so they never appear in this output or
# in the process list. (The CLI can't read --cli-input-json from file:///dev/stdin on macOS.)
SECRET_DIR="$(mktemp -d)"
chmod 700 "$SECRET_DIR"
trap 'rm -rf "$SECRET_DIR"' EXIT
put_secret() {  # name; the value is read from stdin
  local value file="$SECRET_DIR/parameter.json"
  value="$(cat)"
  [[ "$value" =~ ^[A-Za-z0-9+/=_-]+$ ]] || { echo "unexpected characters in secret $1" >&2; exit 1; }
  ( umask 077
    printf '{"Name": "%s", "Type": "SecureString", "Tier": "Standard", "Overwrite": true, "Value": "%s"}' \
      "$SECRET_PREFIX/app/$1" "$value" >"$file" )
  aws ssm put-parameter --cli-input-json "file://$file" >/dev/null
  rm -f "$file"
}
have_secret() { aws ssm get-parameter --name "$SECRET_PREFIX/app/$1" --query Parameter.Name >/dev/null 2>&1; }

if ! have_secret cognito-client-secret; then
  aws cognito-idp describe-user-pool-client --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" \
    --query UserPoolClient.ClientSecret | put_secret cognito-client-secret
fi
if ! have_secret flask-secret-key; then
  # Signs session cookies. Never replaced by this script: replacing it signs everyone out
  openssl rand -hex 32 | put_secret flask-secret-key
fi
echo "secrets:        $SECRET_PREFIX/app/{cognito-client-secret,flask-secret-key}"

# 6. Prod only: what the server may do, and its metadata settings
if [[ "$ENVIRONMENT" == prod ]]; then
  aws iam put-role-policy --role-name "$NAME-instance" --policy-name tenants --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {\"Sid\": \"TenantTable\", \"Effect\": \"Allow\", \"Action\": [
        \"dynamodb:GetItem\", \"dynamodb:PutItem\", \"dynamodb:UpdateItem\", \"dynamodb:DeleteItem\",
        \"dynamodb:Query\", \"dynamodb:BatchWriteItem\", \"dynamodb:ConditionCheckItem\"],
        \"Resource\": \"arn:aws:dynamodb:$REGION:$ACCOUNT_ID:table/$TABLE\"},
      {\"Sid\": \"Secrets\", \"Effect\": \"Allow\", \"Action\": [
        \"ssm:GetParameter\", \"ssm:GetParametersByPath\", \"ssm:PutParameter\",
        \"ssm:DeleteParameter\", \"ssm:DeleteParameters\"],
        \"Resource\": [\"arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter$SECRET_PREFIX\",
                     \"arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter$SECRET_PREFIX/*\"]},
      {\"Sid\": \"AccountDeletion\", \"Effect\": \"Allow\", \"Action\": [
        \"cognito-idp:AdminGetUser\", \"cognito-idp:AdminDeleteUser\"],
        \"Resource\": \"arn:aws:cognito-idp:$REGION:$ACCOUNT_ID:userpool/$POOL_ID\"}
    ]
  }"
  echo "instance role:  $NAME-instance (policy: tenants)"
  # Hop limit 1 keeps the metadata service to the server itself (the default here was 2)
  aws ec2 modify-instance-metadata-options --instance-id "$INSTANCE_ID" \
    --http-tokens required --http-endpoint enabled --http-put-response-hop-limit 1 >/dev/null
  echo "metadata:       IMDSv2 required, hop limit 1"
fi

# Transactions write each item with its own permission (PutItem, DeleteItem, ...) plus
# ConditionCheckItem, which is why TransactWriteItems itself isn't listed above.

echo
echo "Non-secret settings for the app ($ENVIRONMENT):"
echo "  CRA_TENANT_TABLE=$TABLE"
echo "  CRA_SECRET_PREFIX=$SECRET_PREFIX"
echo "  CRA_COGNITO_POOL_ID=$POOL_ID"
echo "  CRA_COGNITO_CLIENT_ID=$CLIENT_ID"
echo "  CRA_COGNITO_DOMAIN=$COGNITO_DOMAIN"
