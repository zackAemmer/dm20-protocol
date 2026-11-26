#!/bin/bash
# EC2 user data (cloud-init), runs once as root on the first boot of an Amazon Linux
# 2023 instance. It only fetches the code; everything else is deploy/install.sh.
# The @PLACEHOLDERS@ are filled in by deploy/aws/provision.sh. No secrets belong here.
set -euxo pipefail
exec > >(tee -a /var/log/dm20-install.log) 2>&1

install -d -m 755 /etc/dm20
cat > /etc/dm20/config.env <<CONFIG
# Non-secret defaults. An SSM parameter of the same purpose overrides each one
# (see deploy/README.md). Secrets are never stored in this file.
DM20_REPO_URL=@REPO_URL@
DM20_REPO_BRANCH=@REPO_BRANCH@
DM20_SSM_PREFIX=@SSM_PREFIX@
DM20_MCP_URL=@MCP_URL@
DM20_PLAY_URL=@PLAY_URL@
DM20_CAMPAIGN_REPO=@CAMPAIGN_REPO@
DM20_IDLE_MINUTES=@IDLE_MINUTES@
CONFIG

dnf install -y -q git
install -d -m 755 /opt/dm20
git clone --depth 1 --branch "@REPO_BRANCH@" "@REPO_URL@" /opt/dm20/app
bash /opt/dm20/app/deploy/install.sh
