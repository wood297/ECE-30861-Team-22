#!/usr/bin/env bash
# One-time AWS setup for the Team 22 server (GETTING_STARTED.md §7). Safe to re-run: each step
# skips what already exists. Needs AWS CLI v2, signed in (`aws login`).
#
#   BUDGET_EMAIL=you@example.com deploy/aws_setup.sh
#
# Creates, in $REGION: an SSH key pair, a security group, a t3.micro Ubuntu 24.04 instance with
# termination protection and an auto-recovery alarm, an Elastic IP, and a monthly cost budget.
set -euo pipefail

REGION="${REGION:-us-east-2}"
NAME="${NAME:-team22-cra}"
KEY_NAME="${KEY_NAME:-team22-ec2}"
KEY_FILE="${KEY_FILE:-$HOME/.ssh/team22-ec2}"
BUDGET_USD="${BUDGET_USD:-15}"

aws() { command aws --region "$REGION" --output text "$@"; }

# 1. SSH key: generated locally, only the public half is uploaded
if [[ ! -f "$KEY_FILE" ]]; then
  ssh-keygen -t ed25519 -f "$KEY_FILE" -N "" -C "$KEY_NAME"
fi
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  aws ec2 import-key-pair --key-name "$KEY_NAME" --public-key-material "fileb://$KEY_FILE.pub" >/dev/null
fi
echo "key pair:       $KEY_NAME ($KEY_FILE)"

# 2. Security group: SSH from this machine's IP only, HTTP/HTTPS from anywhere (Caddy needs 80 for certificates)
MY_IP="$(curl -fsS https://checkip.amazonaws.com)"
SG_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$NAME" --query 'SecurityGroups[0].GroupId')"
if [[ "$SG_ID" == "None" ]]; then
  SG_ID="$(aws ec2 create-security-group --group-name "$NAME" --description "Team 22 CRA server" --query GroupId)"
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions \
    "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$MY_IP/32,Description=ssh-from-dev}]" \
    "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}]" \
    "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]" >/dev/null
fi
echo "security group: $SG_ID (ssh from $MY_IP)"

# 3. Instance
INSTANCE_ID="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId')"
if [[ "$INSTANCE_ID" == "None" ]]; then
  AMI_ID="$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value)"
  INSTANCE_ID="$(aws ec2 run-instances --image-id "$AMI_ID" --instance-type t3.micro \
    --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":8,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
    --credit-specification CpuCredits=standard \
    --metadata-options HttpTokens=required \
    --disable-api-termination \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId')"
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
echo "instance:       $INSTANCE_ID"

# Recover onto new hardware if AWS's side of the instance fails
aws cloudwatch put-metric-alarm --alarm-name "$NAME-auto-recover" \
  --namespace AWS/EC2 --metric-name StatusCheckFailed_System --dimensions "Name=InstanceId,Value=$INSTANCE_ID" \
  --statistic Maximum --period 60 --evaluation-periods 2 --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions "arn:aws:automate:$REGION:ec2:recover"

# 4. Elastic IP, so the address (and so the URL) survives stop/start
ALLOC_ID="$(aws ec2 describe-addresses --filters "Name=tag:Name,Values=$NAME" --query 'Addresses[0].AllocationId')"
if [[ "$ALLOC_ID" == "None" ]]; then
  ALLOC_ID="$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" --query AllocationId)"
fi
aws ec2 associate-address --allocation-id "$ALLOC_ID" --instance-id "$INSTANCE_ID" --allow-reassociation >/dev/null
PUBLIC_IP="$(aws ec2 describe-addresses --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp')"
echo "elastic ip:     $PUBLIC_IP"

# 5. Monthly cost budget, emailing at 80% of actual spend
if [[ -n "${BUDGET_EMAIL:-}" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account)"
  if ! aws budgets describe-budget --account-id "$ACCOUNT_ID" --budget-name "$NAME-monthly" >/dev/null 2>&1; then
    aws budgets create-budget --account-id "$ACCOUNT_ID" \
      --budget "{\"BudgetName\":\"$NAME-monthly\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
      --notifications-with-subscribers "[{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$BUDGET_EMAIL\"}]}]"
  fi
  echo "budget:         $NAME-monthly (\$$BUDGET_USD, alert at 80% to $BUDGET_EMAIL)"
fi

echo
echo "ssh:  ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
echo "url:  https://team22.${PUBLIC_IP//./-}.sslip.io"
