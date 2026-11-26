# Cloud hosting notes

Status as of 2026-11-26. Written so the claude.ai project docs can be updated from it.

## Summary

dm20 runs on a small cloud server and can be used from claude.ai (web or mobile app) as
a custom connector, with the Mac asleep. The server, its Cloudflare routes and its
secrets are created by Terraform in the `claude-ops` repo; this repo holds the engine
changes and what runs on the machine. It is deployed and tested. Three setup steps are
left for the owner (below).

Local play is unchanged: Claude Desktop still starts dm20 over stdio.

## What exists

| Thing | Value |
|---|---|
| Engine branch | `cloud` in `zackAemmer/dm20-protocol` (the server runs this branch) |
| Infrastructure code | `claude-ops`: `projects/dnd/services/dm20/service.yaml`, `platform/services.tf`, `platform/cloudflare.tf` |
| Instance | `cops-dnd-dm20`, t4g.small, us-west-2, account 905096110277 |
| MCP URL | `https://quest-mcp.zackaemmer.io/mcp` |
| Party Mode URL | `https://quest.zackaemmer.io` |
| OAuth client ID | `dm20-claude` (no client secret) |
| Campaign repo (to be created) | `git@github.com:zackAemmer/dnd-campaigns.git`, private |
| Settings and secrets | SSM Parameter Store under `/cops/projects/dnd/` (see `deploy/README.md`) |

`cloud` and `main` share one history: `main` at `034345d` already contains the Party
Mode image channel and UI restyle (`ea3689a`, `d375f44`) merged with the cloud work
(fastmcp 2.14 and `websockets`; HTTP mode with owner OAuth; the arc/art/backup tools;
`deploy/`). `cloud` adds the latest deploy and documentation changes on top, so `main`
can be fast-forwarded to it.

### Hostnames

Cloudflare's free certificate covers `*.zackaemmer.io` only, so the names are one level
deep: `quest` (players) and `quest-mcp` (the connector). They are separate because
Cloudflare Access must cover Party Mode but not the routes Claude calls.

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

1. **Start** (claude-ops connector): `start_service` with project `dnd`, service `dm20`.
   It returns when the server answers, usually within a minute.
2. **Play.** dm20 tools come from the `dm20` connector. `start_party_mode` returns
   player links on `https://quest.zackaemmer.io`.
3. **End.** `backup_campaigns` (dm20), then `stop_service` (claude-ops).
4. **If nobody stops it:** after 90 minutes without a dm20 tool call the server backs
   up and powers itself off. A backup also runs every time the server stops.

Notes for the DM instructions:

- Start the server before opening the chat that will run the session, or the dm20
  tools may be missing from that chat. If they are missing, start a new chat.
- Call `load_campaign` at session start as usual. Party Mode tokens are new after every
  start, so send players fresh links.
- Players pass a Cloudflare email check (a code sent to their address) about once a
  month before their link opens.
- Dice still come only from `roll_dice`. Never edit campaign JSON by hand; the campaign
  repo on GitHub is a backup, not a place to edit while the server is running.

## Measured

| Check | Result |
|---|---|
| Stop → start: machine up and boot setup finished | 15 s |
| dm20 server start to ready | 4 to 9 s |
| Start to ready, total | about 30 s |
| Memory in use by the server | about 260 MB of 1.8 GB |
| `https://quest-mcp.zackaemmer.io/health` through the tunnel | 200 |
| `/mcp` from an address outside Anthropic's ranges | 403 (blocked at Cloudflare) |
| `/mcp` from Anthropic's range without a token | 401 (asks for sign-in) |
| `https://quest.zackaemmer.io` without a login | Redirects to the Cloudflare Access login |
| Smoke test on the instance (`deploy/smoke_test.py`) | Passed: OAuth sign-in, 103 tools, roll, combat action, character update, session note, arc/art tools, Party Mode push over WebSocket |
| Test suite | 5065 passed, 3 skipped, 2 failed (the two known broken httpx mocks in `test_adventure_parser.py`) |
| stdio mode | Works |

Not yet tested, because it needs the owner's steps: the connector sign-in from
claude.ai, backup to the real campaign repo, players getting through Access, and a full
session from phones.

## Security

- The instance accepts no inbound connections. Everything arrives through the tunnel.
- MCP: OAuth 2.1 with PKCE. One pre-registered client (`dm20-claude`), codes only go to
  Claude's callback URLs, no dynamic registration. Signing in needs the owner password;
  10 wrong attempts lock sign-in for 15 minutes. Tokens are random, stored hashed on
  the instance disk, access tokens last 8 hours, refresh tokens 90 days (rotated).
- Cloudflare blocks `/mcp` for every address outside Anthropic's published outbound
  ranges. The sign-in page stays reachable from a browser, protected by the password.
- Party Mode: Cloudflare Access (email allow-list) in front of the existing player tokens.
- Secrets are generated by Terraform into SSM Parameter Store; on the instance they
  exist only in root-only files on tmpfs (and the deploy key in the `dm20` user's home).
- The arc is only returned by `read_campaign_arc`; Party Mode never serves it.

## Setup steps left for the owner

### 1. Players' email addresses

AWS console (sandbox account, us-west-2) > Systems Manager > Parameter Store > Create
parameter. Name `/cops/projects/dnd/access-emails`, type String, value: the addresses
separated by commas (include your own). Then ask a Code session on `claude-ops` to run
`./platform/deploy.sh`. Until then nobody can open Party Mode.

### 2. Campaign repository

1. GitHub > New repository > name `dnd-campaigns` > **Private** > Create (empty, no README).
2. In the repo: Settings > Deploy keys > Add deploy key. Title `dm20 server`. Paste the
   public key from the claude-ops Terraform output `services` (`deploy_key_public`).
   Tick **Allow write access**. Add key.
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

The server clones the repo at its next start. From then on its copy is the live one.
Before local play on the Mac run `git pull`; afterwards
`git add -A && git commit -m "Local session" && git push`.

Only `campaigns/` is migrated. PDF rulebooks in `library/` stay on the Mac.

### 3. Connector in claude.ai

1. Start the server (`start_service`, or AWS console > EC2 > `cops-dnd-dm20` > Start).
   `https://quest-mcp.zackaemmer.io/health` should show `{"ok":true}`.
2. Read the password: Parameter Store > `/cops/projects/dnd/owner-password` > Show
   decrypted value.
3. claude.ai > Customize > Connectors > **+ Add** > Add custom connector. Name `dm20`.
   URL `https://quest-mcp.zackaemmer.io/mcp`. Continue.
4. OAuth client: **Use your own OAuth client**. Client ID `dm20-claude`. Leave the
   client secret empty. Add, then Connect, and enter the password.
5. Reconnect the claude-ops connector too, so chats get `start_service` and `stop_service`.

If the connector fails with a 403 from Cloudflare, check the zone's Security > Bots
settings and turn off **Bot Fight Mode** and **Block AI bots**.

### 4. End-to-end check (fresh chat, Mac asleep)

Start the server; confirm the dm20 tools load; `load_campaign`; `read_campaign_arc`;
`start_party_mode` and open two player links on phones over mobile data; a labelled
`roll_dice`; an image push; `backup_campaigns` (check the commit on GitHub);
`stop_service`.

## Open items

- Pictures in Party Mode: on the hosted server pass `image_base64` to `party_push` /
  `party_resolve_action` (an `image_path` would have to exist on the server).
- Later, not in this build: anti-fudge roll log and `skill_check`, per-player language,
  a deploy script for friends.
