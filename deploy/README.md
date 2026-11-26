# deploy/: running dm20 in the cloud

Hosts dm20 on one small EC2 instance so that it can be added to claude.ai as a custom
connector and played from phones. The instance runs only during play.

For the full picture (what was built, how to start and stop, setup steps for the
owner) read [`docs/cloud-hosting-notes.md`](../docs/cloud-hosting-notes.md).

## How it fits together

```
claude.ai ──https──> Cloudflare ──tunnel──> cloudflared ──> dm20 MCP server  127.0.0.1:8000
phones    ──https──> Cloudflare ──tunnel──> cloudflared ──> Party Mode        127.0.0.1:8080
                                              (EC2 instance: no inbound ports)
```

- `dm20.service` runs `dm20-protocol` in HTTP mode as the unprivileged user `dm20`.
  Data lives in `/var/lib/dm20` (`DM20_STORAGE_DIR`); campaigns in
  `/var/lib/dm20/campaigns`, a clone of the private campaign repository.
- `dm20-cloudflared.service` runs the Cloudflare Tunnel (outbound connection only).
- `dm20-boot.service` runs at every boot: updates the code, reads settings and secrets
  from SSM Parameter Store into `/run/dm20` (tmpfs, root only), syncs the campaign repo.
- `dm20-idle.timer` powers the machine off after 90 minutes without an MCP tool call.
- Whenever the server stops (power-off, idle, restart) it commits and pushes the
  campaigns (`ExecStopPost` in `dm20.service`).

## Who creates what

The AWS instance, its role, settings, secrets and the Cloudflare side (tunnel, DNS
names, firewall rule, email allow-list) are created by Terraform in the `claude-ops`
repo (`platform/services.tf`, described by `projects/dnd/services/dm20/service.yaml`).
This folder is only what runs on the machine.

The contract with the platform: on first boot it clones this repo to `/opt/dm20/app`
and runs `deploy/install.sh` as root with `COPS_SSM_PREFIX`, `COPS_REGION`,
`COPS_IDLE_MINUTES`, `COPS_REPO_URL` and `COPS_REPO_BRANCH` set. Any host that does the
same, and provides the SSM parameters below, can run dm20 this way.

## Files

| File | Purpose |
|---|---|
| `install.sh` | Installs packages, `uv`, the Python environment and the systemd units. Idempotent. |
| `bin/dm20-boot` | Boot setup: code update, settings and secrets from SSM, deploy key, campaign repo sync. |
| `bin/dm20-idle-check` | Idle power-off check. |
| `bin/dm20-backup` | Commit + push of the campaigns directory. |
| `systemd/` | Unit files. |
| `smoke_test.py` | Signs in over OAuth and plays a mock session against a running server. |

## Settings (SSM Parameter Store, under `COPS_SSM_PREFIX`, here `/cops/projects/dnd/`)

| Parameter | Type | Purpose |
|---|---|---|
| `owner-password` | SecureString | Password for the connector sign-in page (16+ characters, no single quotes). |
| `tunnel-token` | SecureString | Cloudflare Tunnel token. |
| `deploy-key` | SecureString | Private SSH key for the campaign repository. |
| `mcp-url` | String | Public URL of the MCP server. |
| `play-url` | String | Public URL of Party Mode. |
| `campaign-repo` | String | SSH URL of the private campaign repository. |
| `source-repo`, `source-branch` | String | Which code to run. Read at every boot, so the branch can change without a rebuild. |
| `auto-update` | String | Optional. `off` stops the code update at boot. |

In the claude-ops setup all of these are written by Terraform. Secrets are never put in
this repository or in logs. Without `owner-password` or `tunnel-token` the instance
boots, starts nothing, and powers itself off when idle.

## Server environment variables (HTTP mode)

| Variable | Meaning |
|---|---|
| `DM20_TRANSPORT=http` (or `--http`) | Serve streamable HTTP instead of stdio. Default is stdio. |
| `DM20_PUBLIC_URL` | Public https URL of the MCP server (OAuth issuer). Required. |
| `DM20_OWNER_PASSWORD` | Sign-in password. Required, 16+ characters. |
| `DM20_HTTP_HOST` / `DM20_HTTP_PORT` | Bind address, default `127.0.0.1:8000`. |
| `DM20_OAUTH_CLIENT_ID` | OAuth client ID, default `dm20-claude`. |
| `DM20_TOKEN_FILE` | Where hashed tokens are kept, default `$DM20_STORAGE_DIR/.auth/tokens.json`. |
| `DM20_ACTIVITY_FILE` | File touched on every tool call (idle timer). |
| `DM20_PARTY_PUBLIC_URL` | Public URL of Party Mode; player links use it. |
| `DM20_PARTY_HOST` | Party Mode bind address, default `0.0.0.0`. |

## Common tasks

```bash
# Shell on the instance (no SSH; uses SSM Session Manager, from a Code session)
aws ssm start-session --target <instance id>
#   journalctl -u dm20 -u dm20-boot -u dm20-cloudflared -b     # logs
#   sudo bash /opt/dm20/app/deploy/install.sh                 # reinstall after a manual change

# Test a local server end to end (throwaway data directory)
export DM20_OWNER_PASSWORD=$(openssl rand -hex 16)
DM20_STORAGE_DIR=$(mktemp -d) DM20_TRANSPORT=http DM20_PUBLIC_URL=http://127.0.0.1:8000 uv run dm20-protocol &
uv run python deploy/smoke_test.py
```

Starting and stopping is done from a chat with the claude-ops connector
(`start_service` / `stop_service`, project `dnd`, service `dm20`). New commits on the
branch named in `source-branch` (`main`) are picked up at the next boot.

## Cost

About $0.64/month for the disk plus about $0.022/hour while running: roughly $1/month
at 16 hours of play. Details are in `projects/dnd/README.md` in `claude-ops`.
