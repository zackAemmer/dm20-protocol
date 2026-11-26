# Pixel art guide: pictures in Claude the DM

The project instructions are the rules for running the game. This doc is the how-to for pictures, which the instructions point to. Every picture is pixel art made with **pxl** (`crsolver/claude-pxl`, MIT): you write a small `.px.js` file and its CLI renders PNGs. Pictures are a treat that supports play. They shouldn't slow it down.

## Setup (once per chat, before the first picture)

1. Load the `pxl-pixel-art` skill with the Skill tool. It clones the CLI into the cloud workspace:
   ```
   [ -d /home/claude/claude-pxl ] || (git clone -q https://github.com/crsolver/claude-pxl.git /home/claude/claude-pxl && git -C /home/claude/claude-pxl checkout -q 21fee41)
   ```
   Pinned to commit `21fee41` (Oct 2026). Bump the pin deliberately in a build chat, not mid-session. If the skill isn't listed, run the clone and read `/home/claude/claude-pxl/SKILL.md`.
2. `PXL` = `node /home/claude/claude-pxl/bin/pxl.js` (Node ≥ 22.18; the workspace has it). Work in `/home/claude/art/`. Read `reference.md` for the full API when needed, and `lighting.md` only for scenes with lamps or glows.
3. `read_campaign_art` (dm20-cloud) and save the result to `/home/claude/art/<campaign-slug>.px.js`. Do this once per chat, then draw only new things.

## How often (a guideline, not a quota)

- Aim for roughly **6-8 new pictures in a typical session**. The players have asked for more pictures, not fewer. Go by the energy at the table: more when the story is hitting, fewer during rapid action.
- **Don't make players wait on art.** Narrate the beat and let them act; send the picture after the narration or while they decide.
- In combat or against a clock, usually skip, unless it's a battle map or a moment worth a picture.
- A picture already in the art file can be rebuilt and re-sent any time at no cost.
- Quick by default (one build, one look, one small fix at most). Give the opening shot, the climax and big reveals the full polish.

## What's worth a picture

- The opening shot of each session.
- Arriving somewhere new, or meeting an important person.
- A moment the players will remember: a great roll, a near-miss, a dramatic catch, a funny failure.
- The climax, or a big reveal *after* the party has uncovered it.
- A battle map at the start of combat when position matters.

## Draw ahead (prep chat)

At the end of a prep chat, add to the campaign art file: an establishing scene per main location, a portrait for each main NPC, and a few reusable props. Then in play most pictures are already drawn and just get built and sent. Draw them without any secrets. Save with `write_campaign_art` (the whole file).

## How to make and send one

1. Add the asset to the campaign art file. Palette first, reused across the campaign so everything looks like one world. Scenes ~96×54 to 192×108; portraits ~64×48; battle maps on an 8px grid.
2. `PXL build <file>.px.js --only <name>`, then **look at** `out/_preview/<name>.png` with Read (it has a pixel grid, so it's for checking only). Fix with small edits. Follow the skill's habits (no filler noise, mirror and outline, ramps for lighting).
3. The clean picture is `out/scenes/<name>.png` (or `out/sprites/`).
   - **Phones:** send it at real size as `image_base64` on `party_push` or `party_resolve_action` (phones scale pixel art cleanly), with a one-line in-world `image_caption` and its Chinese in `translations`: `{"zh": {"image_caption": "..."}}`. `image_path` doesn't work (the file would have to be on the server).
   - **Chat:** upscale with nearest-neighbour (PIL `Image.NEAREST`, scale 8-10) to `/home/claude/art/<name>_x8.png` and send it with `SendUserFile`, `display: render`, captioned Chinese first, then English.
4. After a hero picture, and always at session end, `write_campaign_art` with the whole updated file. PNGs don't need saving; they're rebuilt from the source.

## Art rules

- Show only what the characters perceive. Never draw secrets before the party uncovers them: hidden creatures, the true culprit, concealed doors, anything from the arc. A reveal picture comes after the reveal.
- Original designs only: no copyrighted characters or real people.
- Pictures never decide outcomes: positions on a battle map come from the game state, not the other way round.
