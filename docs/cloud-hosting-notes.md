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
| Engine branch | `main` in `zackAemmer/dm20-protocol` (the server runs this branch) |
| Infrastructure code | `claude-ops`: `projects/dnd/services/dm20/service.yaml`, `platform/services.tf`, `platform/cloudflare.tf` |
| Instance | `cops-dnd-dm20`, t4g.small, us-west-2, account 905096110277 |
| MCP URL | `https://quest-mcp.zackaemmer.io/mcp` |
| Party Mode URL | `https://quest.zackaemmer.io` |
| OAuth client ID | `dm20-claude` (no client secret) |
| Campaign repo (to be created) | `git@github.com:zackAemmer/dnd-campaigns.git`, private |
| Settings and secrets | SSM Parameter Store under `/cops/projects/dnd/` (see `deploy/README.md`) |

Everything is on `main`: the Party Mode image channel and UI restyle, fastmcp 2.14 and
`websockets`, HTTP mode with owner OAuth, the arc/art/backup tools, and `deploy/`. The
separate `cloud` and `party-ui` branches were merged and deleted on 2026-11-26.

### Hostnames

Cloudflare's free certificate covers `*.zackaemmer.io` only, so the names are one level
deep: `quest` (players) and `quest-mcp` (the connector), kept separate so each can have
its own access rules.

## New tools

| Tool | What it does |
|---|---|
| `read_campaign_arc()` | Returns `CAMPAIGN_ARC.md` of the loaded campaign. DM only: its text must never go into anything players see. |
| `read_campaign_art(name?)` | Returns the art source, by default `art/<campaign-slug>.px.js`. |
| `write_campaign_art(content, name?)` | Replaces the art source file (whole file). |
| `backup_campaigns(message?)` | Commits and pushes all campaigns to the private repo. Call at Session End. |

Party Mode tools added or changed on 2026-11-26:

| Tool | What it does |
|---|---|
| `party_wait_for_actions(timeout_seconds?, gather_seconds?)` | Waits for the players and returns every action sent at about the same time as one batch. Use it instead of polling. |
| `party_pop_action(all_pending?)` | Takes the next action, or all waiting actions with `all_pending=true`. |
| `party_resolve_action(action_id, ...)` | `action_id` may be several ids separated by commas: one narrative answers them all. |
| `party_push` / `party_resolve_action` `translations` | The narrative in each other language in use, e.g. `{"zh": "…"}`. Each device shows only its own language. |

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
- A player's link is their key: it carries a secret token that is stable across sessions
  (so it can be bookmarked). If a link leaks, `party_refresh_token` replaces it.
- **Languages.** Each player picks English, 简体中文 or 繁體中文 on their own device
  (Table options). `get_party_status` and the action tools list the languages in use.
  Write the narrative in English and pass the other languages as `translations`; write
  private messages in that player's language. If a translation is missing, the tool
  result says who saw the English text.
- **Read-aloud.** Players tap the speaker on a message, or switch on "Read new messages
  aloud", to hear it in their language in a neural voice (English: en-GB Ryan; Chinese:
  zh-CN Yunxi). The DM does nothing for this.
- **Actions.** The players talk things through out loud and then type what they do, so
  the phone carries the result, not the discussion. `party_wait_for_actions` returns
  what arrives; two actions sent at the same moment come back together and are answered
  with one `party_resolve_action` (ids separated by commas).
- **Whispers.** A whisper comes back first and on its own, marked `whisper`. Answer it
  before anything else. Whatever is given as its narrative goes only to that player; the
  table sees nothing. If it changes what others can see, tell the table with `party_push`.
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
| `https://quest.zackaemmer.io/play` without a token | 401 |
| Smoke test on the instance (`deploy/smoke_test.py`) | Passed: OAuth sign-in, 103 tools, roll, combat action, character update, session note, arc/art tools, Party Mode push over WebSocket |
| Test suite | 5065 passed, 3 skipped, 2 failed (the two known broken httpx mocks in `test_adventure_parser.py`) |
| stdio mode | Works |

Not yet tested: a full session from phones over mobile data.

## Security

- The instance accepts no inbound connections. Everything arrives through the tunnel.
- MCP: OAuth 2.1 with PKCE. One pre-registered client (`dm20-claude`), codes only go to
  Claude's callback URLs, no dynamic registration. Signing in needs the owner password;
  10 wrong attempts lock sign-in for 15 minutes. Tokens are random, stored hashed on
  the instance disk, access tokens last 8 hours, refresh tokens 90 days (rotated).
- Cloudflare blocks `/mcp` for every address outside Anthropic's published outbound
  ranges. The sign-in page stays reachable from a browser, protected by the password.
- Party Mode: per-player secret tokens in the links (16 characters, derived from a secret
  kept on the server). There is no Cloudflare login in front; it was removed because it
  got in the players' way. The token-free pages show only a player count.
- Secrets are generated by Terraform into SSM Parameter Store; on the instance they
  exist only in root-only files on tmpfs (and the deploy key in the `dm20` user's home).
- The arc is only returned by `read_campaign_arc`; Party Mode never serves it.

## Setup (done 2026-11-26)

The campaign repository `zackAemmer/dnd-campaigns` exists with the server's deploy key
and holds the imported campaigns. The connector is added in claude.ai as `dm20-cloud`
(URL `https://quest-mcp.zackaemmer.io/mcp`, "Use your own OAuth client", client ID
`dm20-claude`, password in SSM `/cops/projects/dnd/owner-password`).

On the Mac, the campaigns folder is a clone of the same repository: before local play
run `git pull`; afterwards `git add -A`, `git commit -m "Local session"`, `git push`.
Only `campaigns/` is in the repository; PDF rulebooks in `library/` stay on the Mac.

If the connector ever fails with a 403 from Cloudflare, check the zone's Security > Bots
settings and turn off **Bot Fight Mode** and **Block AI bots**.

## End-to-end check (fresh chat, Mac asleep)

Start the server; confirm the dm20 tools load; `load_campaign`; `read_campaign_arc`;
`start_party_mode` and open two player links on phones over mobile data; a labelled
`roll_dice`; an image push; `backup_campaigns` (check the commit on GitHub);
`stop_service`.

## Open items

- Pictures in Party Mode: on the hosted server pass `image_base64` to `party_push` /
  `party_resolve_action` (an `image_path` would have to exist on the server).
- Later, not in this build: anti-fudge roll log and `skill_check`, per-player language,
  a deploy script for friends.
