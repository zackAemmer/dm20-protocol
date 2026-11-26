#!/bin/bash
# Install or update dm20 on an Amazon Linux 2023 instance. Idempotent; run as root.
#   install.sh             install/update everything and start the services
#   install.sh --no-start  the same, without starting services (used at boot)
set -euo pipefail

APP=/opt/dm20/app
DATA=/var/lib/dm20
export UV_PYTHON_INSTALL_DIR=/opt/dm20/python
export UV_CACHE_DIR=/opt/dm20/uv-cache
export HOME=${HOME:-/root}

source /etc/dm20/config.env

# --- packages -------------------------------------------------------------------
dnf install -y -q git tar gzip openssh-clients
if ! command -v cloudflared >/dev/null; then
    curl -fsSL https://pkg.cloudflare.com/cloudflared.repo -o /etc/yum.repos.d/cloudflared.repo
    dnf install -y -q cloudflared
fi
if [ ! -x /usr/local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh |
        env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

# --- user and data directory -------------------------------------------------------
# The server runs as the unprivileged user dm20. The code and virtualenv belong to
# root, so the server cannot change what root runs at boot.
id dm20 >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /sbin/nologin dm20
install -d -o dm20 -g dm20 -m 750 "$DATA"

# --- python environment --------------------------------------------------------------
cd "$APP"
/usr/local/bin/uv sync --frozen --no-dev --compile-bytecode --quiet
chmod -R a+rX /opt/dm20

# --- deploy key for the private campaign repository -----------------------------------
# Generated here so the private key never leaves the instance. The public half is
# published to SSM so it can be added to the repository as a deploy key.
if [ ! -f "$DATA/.ssh/id_ed25519" ]; then
    install -d -o dm20 -g dm20 -m 700 "$DATA/.ssh"
    runuser -u dm20 -- ssh-keygen -q -t ed25519 -N '' -C dm20-campaign-backup -f "$DATA/.ssh/id_ed25519"
fi
REGION=$(cloud-init query region 2>/dev/null || echo us-west-2)
aws ssm put-parameter --region "$REGION" --name "$DM20_SSM_PREFIX/deploy-key-pub" --type String \
    --overwrite --value "$(cat "$DATA/.ssh/id_ed25519.pub")" >/dev/null ||
    echo "WARNING: could not publish the deploy key to SSM; read $DATA/.ssh/id_ed25519.pub instead"

# --- systemd units -----------------------------------------------------------------------
install -m 644 "$APP"/deploy/systemd/*.service "$APP"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q dm20-boot.service dm20.service dm20-cloudflared.service dm20-idle.timer

if [ "${1:-}" != "--no-start" ]; then
    systemctl start dm20-boot.service
    systemctl start --no-block dm20.service dm20-cloudflared.service
    systemctl start dm20-idle.timer
fi
echo "dm20 install finished: $(git -C "$APP" rev-parse --short HEAD)"
