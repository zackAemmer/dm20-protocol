# Cloud hosting for dm20: build brief (2026-11-25)

Use in a **build chat** (never a play chat). Read the project instructions and `claude/base-engine-audit.md` first.

## Why
Play from phones without the Mac awake. The engine moves from a local Claude Desktop MCP server to a small cloud server, added once as a custom connector in claude.ai so it works in any chat on any device.

## Decisions (agreed with Zack, 2026-11-25)
- **Host:** one small EC2 instance (ARM, t4g.small or smaller) in the claude-ops AWS account, running dm20 + Party Mode. The account has a $50/month spending freeze; target is ~$1–2/month.
- **Start/stop from chat** via the claude-ops `aws_call` tool. Session End stops it. Idle auto-shutdown after ~90 min with no MCP calls. Use EC2, not Lightsail (Lightsail bills while stopped).
- **Networking: Cloudflare Tunnel** (`cloudflared`), no public IP, no inbound security-group rules, no Caddy. Hostnames under `zackaemmer.io` (DNS is on Cloudflare), e.g.:
  - `mcp.dnd.zackaemmer.io` → dm20 MCP (HTTP mode)
  - `play.dnd.zackaemmer.io` → Party Mode (WebSocket must pass through)
- **Party Mode** behind Cloudflare Access (free Zero Trust plan, email allowlist), on top of its existing tokens.
- **MCP route:** OAuth sign-in (check claude.ai custom-connector requirements in Anthropic docs first) plus a Cloudflare rule limiting it to Anthropic's published outbound IP ranges.
- **Dependency:** add `websockets` to `pyproject.toml` (Zack OK'd; needed for the live Party Mode feed).
- **Data:** working copy on the instance disk. New private repo (e.g. `zackAemmer/dnd-campaigns`), one folder per campaign; commit + push at Session End and on shutdown. Exclude `party/images/`.
- **Arc and art:** live in the campaign folder on the server. Add engine tools to read the arc privately (DM only, never pushed to Party Mode, never in narration) and to read/write the campaign art file, replacing the Mac Filesystem steps in the old DM playbook.
- **Deploy files** live in the fork under `deploy/` (user-data/cloud-init, systemd units, cloudflared config template, idle-shutdown script). The claude-ops repo stays untouched.
- **Multiple campaigns:** as-is, one loaded at a time.

## Secrets handling
- No tokens, keys or OAuth secrets in git, chat or project docs.
- Claude does not enter tokens. Zack puts the Cloudflare tunnel token (and any OAuth secret) into AWS SSM Parameter Store himself; the instance reads them at boot.
- Campaign-repo access: generate a deploy key on the instance; Zack adds the public key to the repo.

## Steps
1. **Check access:** confirm `aws_call` can create and start/stop EC2, security groups, IAM role (for SSM read). Confirm which MCP framework dm20 uses and how it supports streamable HTTP + OAuth.
2. **Code** on branch `cloud` in the fork: HTTP mode + OAuth, arc/art tools, backup tool, `websockets` dependency, idle timer. Keep stdio mode working so local Claude Desktop play still works as a fallback. Tests.
3. **Local test** in the workspace: run the server, call tools over HTTP with an MCP client, mock a session (roll, combat, update, note).
4. **Campaign repo:** Zack creates the private repo; migrate "The Lantern of Wickmoor" from `~/Desktop/claude/dnd/campaigns/campaigns/` (arc and art included) via the device bridge; commit.
5. **Launch** the instance via `aws_call` with the user-data script; measure boot-to-ready time.
6. **Zack (Claude gives click-by-click steps):** create the tunnel, DNS routes, Access policy, IP rule; put the tunnel token in SSM.
7. **Zack:** add the custom connector in claude.ai and sign in.
8. **End to end:** start the server, open a fresh chat in this project, confirm tools load, run Session Start, Party Mode on two phones over cell data, a labelled roll, an image push, Session End backup + stop.
9. **Docs:** update the DM instructions (start/stop server, arc/art via tools, backup step, "tools missing" means start the server first) and add a change-log entry to `claude/base-engine-audit.md`. (Instructions done 2026-11-26: `docs/DM_PROJECT_INSTRUCTIONS.md`, pasted into the project.)

## Done when
- A full session runs from phones alone with the Mac asleep.
- Server starts in about a minute from chat and stops at Session End or when idle.
- Campaign state is backed up to the private repo after each session.
- Nothing on the server is reachable without Access login (Party Mode) or OAuth + Anthropic IPs (MCP).
- `pytest` passes; local Desktop mode still works.

## Later (separate builds)
- Anti-fudge: engine rolls shown in the Party Mode feed; `skill_check` tool that locks the DC (hidden, logged) before rolling; log monster stats at combat start. Fits the planned seeded dice + roll log.
- Per-player language in Party Mode (translation on the phone; Claude-written versions for key moments).
- Deploy script so friends can run their own copy.
