# Base engine audit (2026-11-15)

Goal: deterministic rules/state engine + Claude as narrator/worldbuilder; local, 2 players typical (up to 4); flexible over time; visuals (SVG maps, generated vistas/portraits).

## Decision: build on dm20-protocol — our fork is zackAemmer/dm20-protocol
https://github.com/zackAemmer/dm20-protocol (forked from teuschb/dm20-protocol on 2026-11-15).

Lineage: Polloinfilzato/dm20-protocol (gone from GitHub) → BNDYH/dm20-protocol (frozen copy at last upstream commit 8200266, 2026-02-24, no LICENSE file) → teuschb/dm20-protocol (8200266 + 29 commits, 46 files, +3669/−408, through 2026-11-10; adds LICENSE.md, MIT) → ours.

### Local setup
- Repo cloned at `~/Desktop/claude/dnd/dm20-protocol` (on `main`, merge commit `034345d` as of 2026-11-26; see change log).
- Campaign data folder: `~/Desktop/claude/dnd/campaigns` (DM20_STORAGE_DIR).
- Test run 2026-11-15 (Linux, Python 3.12.15, voice tests excluded): 4915 passed, 3 skipped, 3 failed — `test_srd_source.py::TestSRDSourceRealAPI::test_real_api_fetch_wizard` (needs live internet) and `test_adventure_parser.py::test_download_caching` / `test_use_cached_data` (broken httpx mocks in the adventure-download tests, not the rules engine).

### teuschb changes over BNDYH (reviewed 2026-11-15)
Party-mode UI fixes and hardening (whisper validation, push-endpoint SSRF guard, secret tokens/presence, live sheet updates), Web Push notifications (new dependency pywebpush; only talks to browser-vendor push services, endpoints allowlisted and validated), limited-use feature tracking + partial short-rest recovery, milestone leveling, SRD 2024 mapper fixes, rulebook loading/manifest fixes, campaign map served at /map, rewritten DM persona (removes fudge-roll guidance; "dice are never invented"). New code is covered by new tests. No new subprocess/eval/exec calls or unexpected outbound hosts found.

### Determinism audit (teuschb code)
- Combat resolution is in code: `combat/pipeline.py` `resolve_attack` / `resolve_save_spell` compute hit vs effective AC, damage modifiers, concentration DC, return structured results.
- Spell slots enforced in code (tool refuses with "no level N spell slots remaining").
- Non-combat checks: Claude picks DC and calls `roll_dice` (prompt-enforced by persona).
- Dice use unseeded `random.randint` in several modules: fair but not replayable. To fix: one seeded RNG + roll log.
- "Claudmaster" autonomous mode has an LLM Arbiter for creative-action adjudication; not used when Claude calls the MCP tools directly (Claude Desktop/Code).
- ~63k lines Python, 169 test files; README: macOS + Claude Desktop tested.

### Why dm20 over rpg-mcp
rpg-mcp has the stricter engine (LLM proposes, engine validates), spatial combat, original author maintaining it — but only ~29 spells in code and SRD 2014 classes/species/237 items; 2014 rules only; minimal player UI; TypeScript. dm20 loads full SRD 2014/2024, Open5e, 5etools or custom JSON; has Party Mode (phones), level-up engine, NPC/location/quest tracking, DM persona; Python.

### Planned additions
Seeded dice + roll log; ~~image tool for portraits/vistas~~ (done for Party Mode, see below); richer maps on /map.

## Change log

### 2026-11-26 — DM instructions for the cloud setup
- The project instructions in claude.ai now hold the full DM rules for the cloud server (copy: `docs/DM_PROJECT_INSTRUCTIONS.md`). They open by telling play, prep and build chats apart, so only play chats start the server and Party Mode.
- Changes from the first cloud draft: hold a failed d20 roll and ask when a player has an after-roll boost (Bardic Inspiration, Lucky); chat narration all Chinese first, then all English; questions to a player go in the narrative, in both languages; prefer `combat_action`; `use_spell_slot`, `short_rest` / `long_rest`; `load_rulebook source=srd version=2024` at start; pictures via `read_campaign_art` / `write_campaign_art`, sent to phones and the chat; ignore old local tools if a chat is linked to the Mac.
- The old `claude/dm-playbook.md` (Mac setup) is deleted. Its picture how-to moved to `claude/pixel-art-guide.md`; everything else is in the instructions.
- Project docs are copied into the fork at `docs/project/` as a record.

### 2026-11-26 — `party-ui` and `cloud` merged into `main`
- Local `main` on the Mac: fast-forwarded to `party-ui` (`d375f44`), then merged `origin/cloud` (`7e88cfc`) with no conflicts → merge commit `034345d`. `main` is 11 commits ahead of GitHub.
- Full suite after the merge (fastmcp 2.14.7, websockets 15.0.1): 4945 passed, 3 skipped; voice tests and the three known network/mock failures excluded.
- `cloud` brings the `websockets` dependency, which resolves the Party Mode live-feed problem found on 2026-11-16 (below).
- **Not pushed yet:** the Mac-side shell has no GitHub sign-in, and push access from Claude's workspace was not granted. Zack to run `git push origin main party-ui` in the repo.
- After pushing: the cloud instance updates from its configured branch at boot (`COPS_REPO_BRANCH`, set in the hosting platform). Point it at `main` if `main` is now the deploy branch.
- Local Claude Desktop play: restart Claude Desktop so the engine reloads (new image/link tools, websockets). If it isn't launched with `uv run`, run `uv sync` in the repo first (fastmcp was upgraded).

### 2026-11-16 — Party Mode UI restyle (branch `party-ui`; merged to main 2026-11-26)
Commits: `ea3689a` (server: image channel + linked actions), `d375f44` (player UI restyle).
- **Images:** `party_push` and `party_resolve_action` take `image_path` (absolute or relative to the campaign folder) or `image_base64`, plus `image_caption`. The file is copied to `<campaign>/party/images/img_<hex>.<ext>` and served token-gated at `/img/<name>`; only generated names are served. Pixel art can be sent at 1x; the phone scales it crisply.
- **Actions in the feed:** every player's action now shows in everyone's feed (whispers only to the sender) and is replayed on reconnect. `POST /action` accepts `linked_to` (another player's action id) for coordinated moves; `party_pop_action` includes the linked action, and tells the player's phone the DM is reading it.
- **UI:** three tabs (Story, Sheet, Bag) with HP/AC/Heroic Inspiration/slots/conditions pinned on top; real character sheet with tap-for-description; quest items first in Bag; parchment narration with scene/NPC/check styling; Sending → Sent → DM is reading → Answered states, Try again, Edit and resend, quick prompts, "Go with this" linking. Fixed: saving-throw proficiencies stored as `DEX`/`CHA` never showed as proficient.
- `sw.js` caches no app files (asset URLs carry a content hash), so it needed no version bump; it is used for push notifications and stays.
- Tests: `pytest tests -k party` 398 passed, 1 skipped (11 new in `tests/party/test_party_images_and_links.py`).
- **Found:** live updates (WebSocket) didn't work on the Mac: the project's environment had no WebSocket library (`websockets` or `wsproto`), so uvicorn refused `/ws` and phones got no live feed. Resolved by the `websockets` dependency from `cloud` (merged 2026-11-26).

### 2026-11-27 — Phone cleanup (branch `phone-cleanup`)
- Removed the per-message read-aloud button, the `speak` WebSocket message, the Edge TTS narrator (`party/narration.py`, the `edge-tts` base dependency; it stays in the optional `voice` extra) and the "Go with this" action linking (UI, `linked_to` validation, queue field, `linked_action` in `party_pop_action`).
- Voice input: the browser ends a recognition session on its own after a pause or time limit, which cut long messages off. The mic now restarts listening unless the player tapped stop, and keeps words that were not final yet. Guard: three restarts that end within 1.5 s stop it.
- DM instructions: at most one picture per reply, no DM notes in the chat, and a "Winding down" section that offers to close the quest, session or phase while leaving threads open.
- Tests: `pytest tests/party` 265 passed (read-aloud and link tests removed).

### 2026-11-27 — Fewer calls per moment (branch `phone-cleanup`)
Goal: a normal moment is three tool calls (collect, roll, send) instead of eight to twelve.
- `party_collect_actions` returns `sheets`: a compact sheet per player character (HP, AC, modifiers, save and skill bonuses with proficiency, spell slots, conditions, notes). Code: `party/moment.py`.
- `roll_dice` takes `rolls`, a list of labelled rolls made in one call (max 20). Advantage and disadvantage on the same roll now cancel to a straight roll.
- `party_resolve_action` and `party_push` take `updates`: character changes (hp, hp_change, temp_hp, conditions, spell slots, inspiration, notes), one event and game state fields. All of it is validated first; if anything is invalid nothing is saved and nothing is sent.
- Tests: `tests/party/test_moment.py` (15 new). The 51 failures in `tests/test_voice`, `test_adventure_parser.py` and `test_ddb_fetcher.py` are the same on `main` in the cloud workspace.

## Alternatives considered
- rpg-mcp (Mnehmos) — MIT, TypeScript; one-line npx install in Claude Desktop (`mnehmos.rpg.mcp@1.0.3`).
- LoreKit (matluz1) — Apache-2.0, Python; clean rules-as-data design; no D&D 5e pack; GM agent via Claude Code.
- Foundry VTT + MCP Bridge (adambdooley) — best visuals; needs Foundry license + Pro/Max.
- Hosted: Friends & Fables (5e rules, up to 6 players, maps, images; subscription).
