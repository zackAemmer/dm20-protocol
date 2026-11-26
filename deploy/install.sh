#!/bin/bash
# Install or update dm20 on an Amazon Linux 2023 instance. Idempotent; run as root.
#   install.sh             install/update everything and start the services
#   install.sh --no-start  the same, without starting services (used at boot)
#
# The hosting platform clones this repo to /opt/dm20/app and runs this script on first
# boot with these environment variables (they are saved to /etc/dm20/config.env):
#   COPS_SSM_PREFIX    SSM Parameter Store path holding the settings and secrets
#   COPS_REGION        AWS region of that path
#   COPS_IDLE_MINUTES  power off after this long without an MCP tool call (default 90)
#   COPS_REPO_URL, COPS_REPO_BRANCH   where this code came from (for updates at boot)
set -euo pipefail

APP=/opt/dm20/app
DATA=/var/lib/dm20
export UV_PYTHON_INSTALL_DIR=/opt/dm20/python
export UV_CACHE_DIR=/opt/dm20/uv-cache
export HOME=${HOME:-/root}

if [ -n "${COPS_SSM_PREFIX:-}" ]; then
    install -d -m 755 /etc/dm20
    cat > /etc/dm20/config.env <<CONFIG
# Non-secret configuration, written by deploy/install.sh. Secrets are never stored here.
DM20_SSM_PREFIX=$COPS_SSM_PREFIX
DM20_REGION=${COPS_REGION:-us-west-2}
DM20_IDLE_MINUTES=${COPS_IDLE_MINUTES:-90}
DM20_REPO_URL=${COPS_REPO_URL:-$(git -C "$APP" remote get-url origin)}
DM20_REPO_BRANCH=${COPS_REPO_BRANCH:-$(git -C "$APP" rev-parse --abbrev-ref HEAD)}
CONFIG
fi
[ -f /etc/dm20/config.env ] || { echo "Set COPS_SSM_PREFIX (see the top of this script)." >&2; exit 1; }
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
