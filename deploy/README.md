# deploy/: running dm20 in the cloud

Hosts dm20 on one small EC2 instance so that it can be added to claude.ai as a custom
connector and played from phones. The instance runs only during play.

For the full picture (what was built, how to start and stop, setup steps for the
owner) read [`docs/cloud-hosting-notes.md`](../docs/cloud-hosting-notes.md).

## How it fits together

```
claude.ai ──https──> Cloudflare ──tunnel──> cloudflared ──> dm20 MCP server  127.0.0.1:8000
phones    ──https──> Cloudflare Access ──tunnel──────────> Party Mode        127.0.0.1:8080
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

## Files

| File | Purpose |
|---|---|
| `aws/provision.sh` | Creates the IAM role, security group (no inbound rules) and instance. Idempotent. |
| `user-data.sh` | First-boot script: writes `/etc/dm20/config.env`, clones this repo, runs `install.sh`. |
| `install.sh` | Installs packages, `uv`, the Python environment, the deploy key and the systemd units. Idempotent. |
| `bin/dm20-boot` | Boot setup (see above). |
| `bin/dm20-idle-check` | Idle power-off check. |
| `bin/dm20-backup` | Commit + push of the campaigns directory. |
| `systemd/` | Unit files. |
| `cloudflared/config.example.yml` | The tunnel routes in config-file form (reference; routes are set in the dashboard). |
| `smoke_test.py` | Signs in over OAuth and plays a mock session against a running server. |

## Settings (SSM Parameter Store, under `/cops/projects/dnd/`)

| Parameter | Type | Set by | Purpose |
|---|---|---|---|
| `owner-password` | SecureString | owner | Password for the connector sign-in page. At least 16 characters, no single quotes. |
| `tunnel-token` | SecureString | owner | Cloudflare Tunnel token. |
| `deploy-key-pub` | String | instance | Public half of the campaign-repo deploy key (not a secret). |
| `mcp-url` | String | optional | Overrides the MCP public URL (default from `provision.sh`). |
| `play-url` | String | optional | Overrides the Party Mode public URL. |
| `campaign-repo` | String | optional | Overrides the campaign repository SSH URL. |
| `auto-update` | String | optional | `off` stops the code update at boot. |

Secrets are never put in this repository, in user data, or in logs. Until
`owner-password` and `tunnel-token` exist, the instance boots, starts nothing, and
powers itself off when idle.

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
# Create everything (once). Needs the AWS CLI with rights to create IAM roles and EC2.
EXPECTED_ACCOUNT_ID=<account id> ./deploy/aws/provision.sh

# Start / stop
aws ec2 start-instances --instance-ids <id>
aws ec2 stop-instances  --instance-ids <id>

# Shell on the instance (no SSH; uses SSM Session Manager)
aws ssm start-session --target <id>
#   journalctl -u dm20 -u dm20-boot -u dm20-cloudflared -b     # logs
#   sudo bash /opt/dm20/app/deploy/install.sh                 # reinstall after a manual change

# Test a local server end to end (throwaway data directory)
export DM20_OWNER_PASSWORD=$(openssl rand -hex 16)
DM20_STORAGE_DIR=$(mktemp -d) DM20_TRANSPORT=http DM20_PUBLIC_URL=http://127.0.0.1:8000 uv run dm20-protocol &
uv run python deploy/smoke_test.py
```

New commits on the `cloud` branch are picked up at the next boot.

## Cost

| Item | Cost |
|---|---|
| 8 GB gp3 disk (always) | about $0.64/month |
| t4g.small while running | $0.0168/hour |
| Public IPv4 while running | $0.005/hour |

About $1/month at 16 hours of play. The instance has a public IPv4 address because the
default VPC has no NAT gateway (which would cost about $32/month); the security group
has no inbound rules, so nothing can connect to it. CPU credits are "unlimited" so a
boot is never throttled; that can add at most a few cents.
