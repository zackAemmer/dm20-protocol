# Party Mode UI restyle: build brief (2026-11-16)

Use in a **build chat** (never a play chat). Read the project instructions and `claude/base-engine-audit.md` first.

## Why
Zack tested Party Mode (phone UI for players) and found it "ugly/clunky". Wants all of: better look, better layout, a real character sheet, a better feed (with room for the pixel-art images the DM draws), and better flow when sending actions.

## Where the code is
Fork `zackAemmer/dm20-protocol`, cloned at `~/Desktop/claude/dnd/dm20-protocol`. UI = four plain static files in `src/dm20_protocol/party/static/`: `index.html` (~10 KB), `style.css` (~36 KB), `app.js` (~79 KB), `sw.js` (~2.5 KB, offline/push helper). No framework, no build step. Server: `src/dm20_protocol/party/server.py` (Starlette, HTTP + WebSocket). Tabs today: Game, Char, Spells, Inv.

## Scope (all five)
1. **Look**: theme to the campaign: parchment and lantern-gold on dark, pixel-art feel (crisp edges, `image-rendering: pixelated` for art), readable type at phone size. No moths or bug imagery anywhere.
2. **Layout**: HP, AC, conditions and spell slots always visible at the top; quest items and inventory a tap away; fewer wasted tabs; big touch targets; works on a phone held in one hand.
3. **Character sheet**: a real sheet: ability-score grid with modifiers, skills with proficiency marks, HP and spell-slot pips, features and Heroic Inspiration shown clearly, tap an item/spell/feature for its description.
4. **Feed**: readable narration (italic scene text, speaker-styled NPC lines, check lines distinct), auto-scroll, and **inline images** (the DM sends a pixel-art PNG with a caption). Needs a small image channel in the server (the engine audit already lists an image tool as planned); keep it simple, files served from the campaign folder.
5. **Action flow**: clear "sent / DM is thinking / resolved" states, easy re-send, a quick-action row (Perception, Insight, Sleight of Hand, etc. are just text prompts), and a way to mark a submission as **linked** to another player's ("goes with X's action") for coordinated moves.

## Constraints
- Keep the HTTP/WebSocket contract and token auth working. Do not change how tokens are issued, and never print tokens in chat docs.
- Work on a git branch (`party-ui`) in the fork; commit small. No PR to upstream. Keep `main` runnable.
- Do not add a build step or heavy dependency; plain HTML/CSS/JS unless there is a strong reason.
- Test by running Party Mode on the Mac and viewing it in the built-in browser at `http://localhost:8080/play?token=...` at phone width (about 390 px) and desktop width; then Zack checks it on his real phone over wifi. Keep `sw.js` caching from serving stale files after changes (bump its cache version).
- Dummy data for design: use the "The Lantern of Wickmoor" campaign (Zack, Human Bard; X, Tiefling Rogue).

## Done when
- Both players' pages look and read well on a phone, with all five scope items working.
- A test action round trip works (submit on phone, DM resolves, narration + an image appear).
- Existing tests that touch party mode still pass (`pytest tests -k party`).
- Short note added to `claude/base-engine-audit.md` (what changed, commit hash).

## Open questions for Zack
- Preferred accent colors, or just "lantern-gold"?
- Is the offline/notification helper (`sw.js`) used, or can it be dropped?
