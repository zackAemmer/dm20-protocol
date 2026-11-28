# dm20-protocol (zackAemmer fork)

D&D 5e rules engine exposed as an MCP server, plus Party Mode (a phone companion UI).
This fork powers the claude.ai project "Claude the DM": Claude narrates, dm20 keeps the
rules and state. It runs on a small cloud server during play.

## Language

Everything is in English: replies to the user, code, comments, commits, docs.

## Git

- `origin` is `zackAemmer/dm20-protocol`. Push only there.
- Upstream is `Polloinfilzato/dm20-protocol`. Never open pull requests, issues or
  comments on upstream.
- Work on a branch; keep `main` runnable. Merge to `main` only when the user says so.
- **The cloud server runs `main`** and pulls it at every boot, so a merge to `main` is a
  deploy. Nothing on a branch is live.

## Layout

| Path | What |
|---|---|
| `src/dm20_protocol/main.py` | Every MCP tool (`@mcp.tool`). Very large: search it, don't read it whole. |
| `src/dm20_protocol/storage.py`, `models.py` | Campaign state and data models. |
| `src/dm20_protocol/party/` | Party Mode: `server.py` (Starlette HTTP + WebSocket), `queue.py`, `bridge.py`, `lang.py`, `moment.py`, `timing.py`, `images.py`, `push.py`. |
| `src/dm20_protocol/campaign_files.py` | Arc, art source, saved pictures (`art/png/`), backup. |
| `src/dm20_protocol/party/static/` | Phone UI: `index.html`, `style.css`, `app.js`, `i18n.js`, `sw.js`. Plain files, no framework, no build step. |
| `deploy/` | Cloud server: boot script, systemd units, installer. See `deploy/README.md`. |
| `docs/DM_PROJECT_INSTRUCTIONS.md` | The DM instructions pasted into the claude.ai project. |
| `docs/project/` | Copies of the claude.ai project docs (engine audit, briefs, pixel-art guide). |
| `tests/party/` | Party Mode tests. |

## Commands

```bash
pip install -e ".[dev]"          # add --break-system-packages in a cloud workspace
python3 -m pytest tests/party -q # the fast, relevant suite
python3 -m pytest tests -q       # everything (about 2 minutes)
node --check src/dm20_protocol/party/static/app.js
```

In a cloud workspace 51 tests fail on `main` too (`tests/test_voice`,
`tests/test_adventure_parser.py`, `tests/test_ddb_fetcher.py`). Compare against `main`
before blaming a change.

## Conventions

- **Tools refuse rather than guess.** Invalid input returns a clear "Not sent / Not
  resolved / Not rolled: reason" and changes nothing. Never half-apply state.
- Tool functions are wrapped by FastMCP: call them in tests as
  `getattr(tool, "fn", tool)(...)`.
- Structured tool arguments are JSON strings (or lists) validated in the tool; unknown
  keys are an error.
- Dice come only from `random` inside `roll_dice` / `combat_action`. No tool takes a
  pre-decided result.
- Secrets: whispers, `dm_only` notes and the campaign arc never reach a player's device
  or a public narrative. Player links contain tokens: never print them in docs, commits
  or logs.
- Phone UI: keep it plain HTML/CSS/JS, phone width first (about 390 px). Every UI string
  goes through `t()` with a Chinese entry in `i18n.js`. Asset URLs carry a content hash,
  so `sw.js` needs no cache bump.
- Don't add base dependencies without asking. Text-to-speech (`edge-tts`) is only in the
  optional `voice` extra.
- Never edit campaign data by hand, on the server or in the campaign backup repository.

## When behaviour changes

1. Add or update tests in `tests/party/`.
2. Update `docs/DM_PROJECT_INSTRUCTIONS.md` if the DM should play differently, and tell
   the user it needs pasting into the claude.ai project (Claude cannot edit that box).
3. Add a dated entry to `docs/project/base-engine-audit.md`: what changed, branch, test
   result.
4. Update `docs/PARTY_MODE.md` for Party Mode features.

## Inherited from upstream, not used here

`.claude/commands/`, `.claude/agents/`, `.claude/epics/`, `.claude/prds/` and
`.claude/scripts/pm/` are the upstream author's project-management workflow and local DM
commands. They target the upstream repository and the local (stdio) server. Don't run
the `/pm:*` commands, and don't use the `/dm:*` commands for cloud play: the claude.ai
project instructions replace them.
