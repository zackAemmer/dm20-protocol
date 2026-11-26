# Cloud hosting notes

Status as of 2026-11-26. Written so the claude.ai project docs can be updated from it.

## Summary

dm20 can now run on a small cloud server and be used from claude.ai (web or mobile app)
as a custom connector, with the Mac asleep. The code, deploy files and the AWS instance
exist and are tested on the instance itself. What remains is setup only the owner can
do: Cloudflare (tunnel, Access, IP rule), two secrets in AWS, the private campaign
repository, and adding the connector in claude.ai. Those steps are below.

Local play is unchanged: Claude Desktop still starts dm20 over stdio.

## What exists

| Thing | Value |
|---|---|
| Branch | `cloud` in `zackAemmer/dm20-protocol` (based on `main` at `8465b93`) |
| Instance | `i-07e9efb40f0d891d7`, name `cops-dnd-server`, t4g.small, us-west-2, account 905096110277. Currently stopped. |
| IAM role / security group | `cops-dnd-server` (the group has no inbound rules) |
| Tags | `project=dnd`, `managed-by=claude-ops` |
| MCP URL (to be routed) | `https://dnd-mcp.zackaemmer.io/mcp` |
| Party Mode URL (to be routed) | `https://dnd-play.zackaemmer.io` |
| OAuth client ID | `dm20-claude` (no client secret) |
| Campaign repo (to be created) | `git@github.com:zackAemmer/dnd-campaigns.git`, private |
| Settings and secrets | SSM Parameter Store under `/cops/projects/dnd/` (see `deploy/README.md`) |

Commits on `cloud`:

- `f64c99d` Upgrade fastmcp to 2.14, add `websockets` and `uvicorn` dependencies
  (`websockets` fixes the Party Mode `/ws` live feed).
- `7ef9d54` HTTP mode with owner OAuth, arc/art/backup tools, idle tracking, public Party URL.
- `c1f7d3b` Deploy files and smoke test.
- `561be8c` Fix a first-boot deadlock in the boot unit.
- Later commits: CPU credit setting and these docs.

### Hostnames: why not `mcp.dnd.zackaemmer.io`

Cloudflare's free certificate covers `*.zackaemmer.io` but not `*.dnd.zackaemmer.io`.
Two-level names would need a paid certificate, so the defaults are `dnd-mcp` and
`dnd-play`. To use other names, set the `mcp-url` and `play-url` parameters.

## New tools

| Tool | What it does |
|---|---|
| `read_campaign_arc()` | Returns `CAMPAIGN_ARC.md` of the loaded campaign. DM only: its text must never go into anything players see. |
| `read_campaign_art(name?)` | Returns the art source, by default `art/<campaign-slug>.px.js`. |
| `write_campaign_art(content, name?)` | Replaces the art source file (whole file). |
| `backup_campaigns(message?)` | Commits and pushes all campaigns to the private repo. Call at Session End. |

These replace the Mac Filesystem steps. They also work in local (stdio) mode;
`backup_campaigns` then needs `campaigns/` to be a git clone.

## How a session runs

1. **Start** (in a claude.ai chat, with the claude-ops connector):
   `aws_call` with service `ec2`, operation `start_instances`,
   params `{"InstanceIds": ["i-07e9efb40f0d891d7"]}`. Ready in about 30 seconds.
2. **Play.** dm20 tools come from the `dm20` connector. `start_party_mode` returns
   player links on `https://dnd-play.zackaemmer.io`.
3. **End.** Call `backup_campaigns`, then `aws_call` `ec2` `stop_instances` with the
   same params.
4. **If nobody stops it:** after 90 minutes without a dm20 tool call the server backs
   up and powers itself off. A backup also runs every time the server stops.

Notes for the DM instructions:

- Start the server before opening the chat that will run the session, or the dm20
  tools may be missing from that chat. If they are missing, start a new chat.
- Each start loads no campaign state from memory: call `load_campaign` at session start
  as usual. Party Mode tokens are new after every start, so send players fresh links.
- Dice still come only from `roll_dice`. Never edit campaign JSON by hand; the campaign
  repo on GitHub is a backup, not a place to edit while the server is running.

## Measured

| Check | Result |
|---|---|
| Stop → start: machine up and boot setup finished | 15 s |
| dm20 server start to ready | 4 to 9 s |
| Start to ready, total | about 25 to 30 s plus a few seconds for the tunnel to connect |
| Memory in use by the server | about 260 MB of 1.8 GB |
| Smoke test on the instance (`deploy/smoke_test.py`) | Passed: OAuth sign-in, 103 tools, roll, combat action, character update, session note, arc/art tools, Party Mode push over WebSocket |
| Test suite | 5065 passed, 3 skipped, 2 failed (the two known broken httpx mocks in `test_adventure_parser.py`) |
| stdio mode | Works (103 tools, `roll_dice` over stdio) |

Not yet tested, because it needs the owner's setup: the tunnel, Cloudflare Access, the
IP rule, the real connector sign-in from claude.ai, backup to the real repo, and a
boot with the server enabled (the server only starts once `owner-password` exists).

## Security

- The instance accepts no inbound connections. Everything arrives through the tunnel.
- MCP: OAuth 2.1 with PKCE. One pre-registered client (`dm20-claude`), codes only go to
  Claude's callback URLs, no dynamic registration. Signing in needs the owner password;
  10 wrong attempts lock sign-in for 15 minutes. Tokens are random, stored hashed on
  the instance disk, access tokens last 8 hours, refresh tokens 90 days (rotated).
- A Cloudflare rule (step 4 below) limits `/mcp` to Anthropic's outbound addresses.
  The sign-in page stays reachable from your browser, protected by the password.
- Party Mode: Cloudflare Access (email allowlist) in front of the existing player tokens.
- Secrets live only in SSM Parameter Store and in root-only files on tmpfs.
- The arc is only returned by `read_campaign_arc`; Party Mode never serves it.

## Setup steps for the owner

Cloudflare's menus move now and then; names below are as of late 2026.

### 1. Push local work (Mac)

GitHub does not have the `party-ui` branch, and its `main` has no `/img` image route, so
some work is only on the Mac. Push it so it can be merged into `cloud`:

```bash
cd ~/Desktop/claude/dnd/dm20-protocol
git push origin main party-ui
```

### 2. Campaign repository

1. GitHub > New repository > name `dnd-campaigns` > **Private** > Create (empty, no README).
2. In the repo: Settings > Deploy keys > Add deploy key. Title `dm20 server`. Paste the
   key from the SSM parameter `/cops/projects/dnd/deploy-key-pub`. Tick **Allow write
   access**. Add key.
3. On the Mac, with Claude Desktop closed:

```bash
cd ~/Desktop/claude/dnd/campaigns/campaigns
git init -b main
printf '*/party/images/\n*/party/*.png\n.DS_Store\n' > .gitignore
git add -A
git status          # check: CAMPAIGN_ARC.md and art/*.px.js listed, party/images not
git commit -m "Import campaigns"
git remote add origin git@github.com:zackAemmer/dnd-campaigns.git
git push -u origin main
```

From then on the server's copy is the live one. Before local play on the Mac run
`git pull`; afterwards `git add -A && git commit -m "Local session" && git push`.

Only `campaigns/` is migrated. PDF rulebooks in `library/` stay on the Mac; copy them
separately if a hosted campaign needs them.

### 3. Cloudflare Tunnel

1. dash.cloudflare.com > Zero Trust > Networks > Tunnels > **Create a tunnel**.
2. Type **Cloudflared** > name `dnd` > Save tunnel.
3. On the "Install and run a connector" screen, copy the token: the long string that
   starts with `eyJ` at the end of the shown command. Do not run the command. Next.
4. Public hostnames > Add a public hostname:
   - Subdomain `dnd-mcp`, domain `zackaemmer.io`, type `HTTP`, URL `localhost:8000`. Save.
   - Add another: subdomain `dnd-play`, domain `zackaemmer.io`, type `HTTP`, URL `localhost:8080`. Save.

   Cloudflare creates the DNS records itself. WebSockets need no setting.

### 4. Cloudflare rule: MCP only from Anthropic

1. dash.cloudflare.com > zackaemmer.io > Security > WAF > Custom rules > **Create rule**.
2. Name `dnd-mcp Anthropic only`. Click **Edit expression** and paste:

```
(http.host eq "dnd-mcp.zackaemmer.io" and starts_with(http.request.uri.path, "/mcp") and not ip.src in {160.79.104.0/21 153.61.208.0/20})
```

3. Action **Block** > Deploy.

The ranges are Anthropic's published outbound addresses
(https://platform.claude.com/docs/en/api/ip-addresses, checked 2026-11-26).

If the connector later fails with a 403 from Cloudflare, check Security > Bots and turn
off **Bot Fight Mode** and **Block AI bots**: both can stop Claude's requests.

### 5. Cloudflare Access for Party Mode

1. Zero Trust > Access > Applications > **Add an application** > Self-hosted.
2. Name `D&D Party Mode`. Session duration `1 month`. Public hostname: subdomain
   `dnd-play`, domain `zackaemmer.io`.
3. Add a policy: name `Players`, action **Allow**, Include > **Emails** > each player's
   email address (and your own). Save.
4. Login method: leave **One-time PIN** on. Players enter their email once per month
   and type the code Cloudflare emails them.

Do not put Access in front of `dnd-mcp`: claude.ai could not get through it.

### 6. Secrets in AWS

AWS console (sandbox account, region us-west-2) > Systems Manager > Parameter Store >
**Create parameter**, twice. Type **SecureString** for both; add tags `project=dnd`
and `managed-by=claude-ops`.

| Name | Value |
|---|---|
| `/cops/projects/dnd/tunnel-token` | The token from step 3. |
| `/cops/projects/dnd/owner-password` | A new password from your password manager: 16+ characters, no single quotes. |

### 7. Connector in claude.ai

1. Start the instance (AWS console > EC2 > `cops-dnd-server` > Instance state > Start,
   or `aws_call` from a chat). Wait a minute. `https://dnd-mcp.zackaemmer.io/health`
   should show `{"ok":true}`.
2. claude.ai > Customize > Connectors > **+ Add** > Add custom connector.
3. Name `dm20`. URL `https://dnd-mcp.zackaemmer.io/mcp`. Continue.
4. OAuth client: **Use your own OAuth client**. Client ID `dm20-claude`. Leave the
   client secret empty. Add.
5. Connect. On the dm20 sign-in page enter the owner password. Allow.

### 8. End-to-end check (fresh chat, Mac asleep)

Start the server; confirm the dm20 tools load; `load_campaign`; `read_campaign_arc`;
`start_party_mode` and open two player links on phones over mobile data; a labelled
`roll_dice`; an image push; `backup_campaigns` (check the commit on GitHub);
`stop_instances`.

## Open items

- Merge `party-ui` and the Mac's `main` into `cloud` once pushed (step 1). The image
  push in the end-to-end check depends on it.
- The AWS resources were created by `deploy/aws/provision.sh` in this repo, not by the
  claude-ops Terraform, as agreed for this build.
- Later, not in this build: anti-fudge roll log and `skill_check`, per-player language,
  a deploy script for friends.
