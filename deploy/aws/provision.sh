#!/bin/bash
# Create (or reuse) the AWS resources for the dm20 cloud server and launch it.
# Idempotent: safe to run again; it never creates a second instance.
#
#   EXPECTED_ACCOUNT_ID=123456789012 ./deploy/aws/provision.sh
#
# Creates, all tagged project=dnd, managed-by=claude-ops:
#   - IAM role + instance profile  cops-dnd-server  (read /cops/projects/dnd/* in SSM,
#     publish its deploy public key, SSM Session Manager for debugging)
#   - security group               cops-dnd-server  (NO inbound rules)
#   - EC2 instance                 cops-dnd-server  (Amazon Linux 2023, ARM)
#
# Cost while stopped: the 8 GB disk, about $0.64/month. While running (t4g.small +
# public IPv4): about $0.022/hour. No secrets are passed to or stored by this script.
set -euo pipefail

NAME=cops-dnd-server
REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}
INSTANCE_TYPE=${INSTANCE_TYPE:-t4g.small}
SSM_PREFIX=${SSM_PREFIX:-/cops/projects/dnd}
REPO_URL=${REPO_URL:-https://github.com/zackAemmer/dm20-protocol}
REPO_BRANCH=${REPO_BRANCH:-cloud}
MCP_URL=${MCP_URL:-https://quest-mcp.zackaemmer.io}
PLAY_URL=${PLAY_URL:-https://quest.zackaemmer.io}
CAMPAIGN_REPO=${CAMPAIGN_REPO:-git@github.com:zackAemmer/dnd-campaigns.git}
IDLE_MINUTES=${IDLE_MINUTES:-90}
TAGS="{Key=project,Value=dnd},{Key=managed-by,Value=claude-ops}"
HERE=$(cd "$(dirname "$0")/.." && pwd)
export AWS_DEFAULT_REGION=$REGION AWS_PAGER=""

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if [ "${EXPECTED_ACCOUNT_ID:-}" != "$ACCOUNT" ]; then
    echo "Refusing to run: set EXPECTED_ACCOUNT_ID to the account you mean (current: $ACCOUNT)." >&2
    exit 1
fi

# --- IAM role and instance profile --------------------------------------------------
if ! aws iam get-role --role-name $NAME >/dev/null 2>&1; then
    aws iam create-role --role-name $NAME \
        --description "dm20 D&D server: read its settings and secrets from SSM" \
        --tags Key=project,Value=dnd Key=managed-by,Value=claude-ops \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
            "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam put-role-policy --role-name $NAME --policy-name dm20-ssm-params --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {\"Effect\": \"Allow\", \"Action\": [\"ssm:GetParameter\", \"ssm:GetParameters\", \"ssm:GetParametersByPath\"],
     \"Resource\": \"arn:aws:ssm:$REGION:$ACCOUNT:parameter$SSM_PREFIX*\"},
    {\"Effect\": \"Allow\", \"Action\": \"ssm:PutParameter\",
     \"Resource\": \"arn:aws:ssm:$REGION:$ACCOUNT:parameter$SSM_PREFIX/deploy-key-pub\"},
    {\"Effect\": \"Allow\", \"Action\": \"kms:Decrypt\", \"Resource\": \"*\",
     \"Condition\": {\"StringEquals\": {\"kms:ViaService\": \"ssm.$REGION.amazonaws.com\"}}}
  ]
}"
aws iam attach-role-policy --role-name $NAME \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
if ! aws iam get-instance-profile --instance-profile-name $NAME >/dev/null 2>&1; then
    aws iam create-instance-profile --instance-profile-name $NAME \
        --tags Key=project,Value=dnd Key=managed-by,Value=claude-ops >/dev/null
    aws iam add-role-to-instance-profile --instance-profile-name $NAME --role-name $NAME
    sleep 10 # IAM is eventually consistent; EC2 must be able to see the profile
fi

# --- security group: no inbound rules at all ---------------------------------------------
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME Name=vpc-id,Values=$VPC \
    --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ]; then
    SG=$(aws ec2 create-security-group --group-name $NAME --vpc-id "$VPC" \
        --description "dm20 server: no inbound; reached only through its Cloudflare Tunnel" \
        --tag-specifications "ResourceType=security-group,Tags=[$TAGS,{Key=Name,Value=$NAME}]" \
        --query GroupId --output text)
fi

# --- instance -------------------------------------------------------------------------------
ID=$(aws ec2 describe-instances \
    --filters Name=tag:Name,Values=$NAME Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$ID" = "None" ]; then
    USER_DATA=$(mktemp)
    sed -e "s|@REPO_URL@|$REPO_URL|g" -e "s|@REPO_BRANCH@|$REPO_BRANCH|g" \
        -e "s|@SSM_PREFIX@|$SSM_PREFIX|g" -e "s|@MCP_URL@|$MCP_URL|g" -e "s|@PLAY_URL@|$PLAY_URL|g" \
        -e "s|@CAMPAIGN_REPO@|$CAMPAIGN_REPO|g" -e "s|@IDLE_MINUTES@|$IDLE_MINUTES|g" \
        "$HERE/user-data.sh" > "$USER_DATA"
    # A public IPv4 address is needed for outbound traffic (the default VPC has no NAT
    # gateway, which would cost about $32/month). With no inbound security-group rules
    # nothing can connect to it.
    ID=$(aws ec2 run-instances \
        --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
        --instance-type "$INSTANCE_TYPE" \
        --iam-instance-profile Name=$NAME \
        --network-interfaces "DeviceIndex=0,AssociatePublicIpAddress=true,Groups=$SG" \
        --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=8,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}' \
        --metadata-options 'HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1' \
        --instance-initiated-shutdown-behavior stop \
        --credit-specification CpuCredits=unlimited \
        --user-data "file://$USER_DATA" \
        --tag-specifications "ResourceType=instance,Tags=[$TAGS,{Key=Name,Value=$NAME}]" \
            "ResourceType=volume,Tags=[$TAGS,{Key=Name,Value=$NAME}]" \
        --query 'Instances[0].InstanceId' --output text)
    rm -f "$USER_DATA"
    echo "Launched $ID"
else
    echo "Instance already exists: $ID"
fi

echo "instance_id=$ID"
echo "Start:  aws ec2 start-instances --instance-ids $ID"
echo "Stop:   aws ec2 stop-instances --instance-ids $ID"
