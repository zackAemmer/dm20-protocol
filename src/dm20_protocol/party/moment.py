"""One-call helpers for resolving a moment of play.

Two things that cut round trips for the DM:

- `sheet_summary`: a compact view of a character (modifiers, skill and save bonuses,
  resources), returned with collected actions so the DM needs no per-character lookup.
- `plan_updates`: validates a bundle of state changes (characters, one event, game state)
  and returns a function that applies them. Nothing is changed unless everything is valid,
  so a narrative is never sent on top of half-applied state.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from dm20_protocol.models import AdventureEvent, EventType

ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")

SKILL_ABILITY = {
    "Acrobatics": "dexterity", "Animal Handling": "wisdom", "Arcana": "intelligence",
    "Athletics": "strength", "Deception": "charisma", "History": "intelligence",
    "Insight": "wisdom", "Intimidation": "charisma", "Investigation": "intelligence",
    "Medicine": "wisdom", "Nature": "intelligence", "Perception": "wisdom",
    "Performance": "charisma", "Persuasion": "charisma", "Religion": "intelligence",
    "Sleight of Hand": "dexterity", "Stealth": "dexterity", "Survival": "wisdom",
}

CHARACTER_KEYS = {
    "name", "hp", "hp_change", "temp_hp", "add_conditions", "remove_conditions",
    "use_spell_slot", "inspiration", "notes",
}
EVENT_KEYS = {
    "event_type", "description", "title", "session_number", "characters_involved",
    "location", "importance", "tags",
}
GAME_STATE_KEYS = {
    "current_location", "current_session", "current_date_in_game", "party_level",
    "party_funds", "in_combat", "notes",
}


def _norm_ability(value: Any) -> Optional[str]:
    """'DEX', 'Dex', 'dexterity', 'Saving Throw: Dex' -> 'dexterity'."""
    key = str(value or "").lower().replace("saving throw:", "").strip()
    for ability in ABILITIES:
        if key == ability or key == ability[:3]:
            return ability
    return None


def _signed(n: int) -> str:
    return f"{n:+d}"


def sheet_summary(character) -> dict:
    """Everything the DM needs to call for and resolve a roll, in a few hundred characters."""
    mods = {a: character.abilities[a].mod for a in ABILITIES if a in character.abilities}
    prof = character.proficiency_bonus
    skill_profs = {str(s).lower() for s in character.skill_proficiencies}
    save_profs = {_norm_ability(s) for s in character.saving_throw_proficiencies}

    skills = {
        skill: _signed(mods.get(ability, 0) + (prof if skill.lower() in skill_profs else 0))
        for skill, ability in SKILL_ABILITY.items()
    }
    saves = {
        a[:3].upper(): _signed(mods.get(a, 0) + (prof if a in save_profs else 0))
        for a in ABILITIES
    }
    summary = {
        "name": character.name,
        "hp": f"{character.hit_points_current}/{character.hit_points_max}",
        "ac": character.armor_class,
        "proficiency_bonus": prof,
        "mods": {a[:3].upper(): _signed(m) for a, m in mods.items()},
        "saves": saves,
        "skills": skills,
        "proficient_skills": sorted(character.skill_proficiencies),
    }
    if character.temporary_hit_points:
        summary["temp_hp"] = character.temporary_hit_points
    if character.spell_slots:
        used = character.spell_slots_used
        summary["spell_slots"] = {
            str(level): f"{max(0, total - used.get(level, 0))}/{total}"
            for level, total in sorted(character.spell_slots.items())
        }
    if character.conditions:
        summary["conditions"] = list(character.conditions)
    if character.inspiration:
        summary["inspiration"] = True
    if getattr(character, "concentration", None):
        summary["concentrating"] = True
    if character.notes:
        summary["notes"] = character.notes
    return summary


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def plan_updates(storage, raw: Any) -> tuple[Optional[Callable[[], list[str]]], Optional[str]]:
    """Validate a bundle of updates. Returns (apply, error); `apply()` returns log lines.

    `raw` is a JSON object (or its text):
      {"characters": [{"name": "Bru", "hp_change": -4, "add_conditions": ["prone"],
                       "use_spell_slot": 2, "inspiration": true, "notes": "..."}],
       "event": {"event_type": "social", "description": "...", "importance": 3},
       "game_state": {"current_location": "...", "notes": "..."}}
    """
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"updates is not valid JSON ({exc})."
    if not isinstance(raw, dict):
        return None, "updates must be a JSON object."
    unknown = set(raw) - {"characters", "event", "game_state"}
    if unknown:
        return None, f"updates: unknown key(s) {', '.join(sorted(unknown))}. Use characters, event, game_state."

    steps: list[Callable[[], str]] = []

    characters = raw.get("characters") or []
    if not isinstance(characters, list):
        return None, "updates.characters must be a list."
    for entry in characters:
        if not isinstance(entry, dict) or not entry.get("name"):
            return None, "updates.characters: each entry needs a name."
        bad = set(entry) - CHARACTER_KEYS
        if bad:
            return None, (
                f"updates.characters[{entry['name']}]: unknown key(s) {', '.join(sorted(bad))}. "
                f"Allowed: {', '.join(sorted(CHARACTER_KEYS))}. Use update_character for anything else."
            )
        character = storage.get_character(str(entry["name"]))
        if not character:
            return None, f"updates.characters: no character called '{entry['name']}'."
        step, error = _plan_character(storage, character, entry)
        if error:
            return None, error
        steps.append(step)

    event = raw.get("event")
    if event is not None:
        if not isinstance(event, dict):
            return None, "updates.event must be an object."
        bad = set(event) - EVENT_KEYS
        if bad:
            return None, f"updates.event: unknown key(s) {', '.join(sorted(bad))}."
        description = str(event.get("description") or "").strip()
        if not description:
            return None, "updates.event needs a description."
        try:
            record = AdventureEvent(
                event_type=EventType(str(event.get("event_type") or "roleplay")),
                title=event.get("title") or (description[:60].rstrip() + ("..." if len(description) > 60 else "")),
                description=description,
                session_number=event.get("session_number"),
                characters_involved=_as_list(event.get("characters_involved")),
                location=event.get("location"),
                importance=int(event.get("importance", 3)),
                tags=_as_list(event.get("tags")),
            )
        except Exception as exc:
            return None, f"updates.event is not valid ({exc})."

        def add_event(record=record) -> str:
            storage.add_event(record)
            return f"event logged: '{record.title}'"
        steps.append(add_event)

    game_state = raw.get("game_state")
    if game_state is not None:
        if not isinstance(game_state, dict):
            return None, "updates.game_state must be an object."
        bad = set(game_state) - GAME_STATE_KEYS
        if bad:
            return None, f"updates.game_state: unknown key(s) {', '.join(sorted(bad))}."
        if game_state:
            def update_state(changes=dict(game_state)) -> str:
                storage.update_game_state(**changes)
                return f"game state: {', '.join(sorted(changes))} updated"
            steps.append(update_state)

    if not steps:
        return None, None

    def apply() -> list[str]:
        with storage.batch_update():
            return [step() for step in steps]

    return apply, None


def _plan_character(storage, character, entry: dict) -> tuple[Optional[Callable[[], str]], Optional[str]]:
    name = character.name
    try:
        hp = character.hit_points_current
        if entry.get("hp") is not None:
            hp = int(entry["hp"])
        if entry.get("hp_change") is not None:
            hp += int(entry["hp_change"])
        hp = max(0, min(hp, character.hit_points_max))
        temp_hp = None if entry.get("temp_hp") is None else max(0, int(entry["temp_hp"]))
        slot_use = entry.get("use_spell_slot")
        slot_levels = [] if slot_use is None else [int(v) for v in (slot_use if isinstance(slot_use, list) else [slot_use])]
    except (TypeError, ValueError):
        return None, f"updates.characters[{name}]: hp, hp_change, temp_hp and use_spell_slot must be whole numbers."

    planned_use: dict[int, int] = {}
    for level in slot_levels:
        planned_use[level] = planned_use.get(level, 0) + 1
    for level, count in planned_use.items():
        total = character.spell_slots.get(level, 0)
        left = total - character.spell_slots_used.get(level, 0)
        if count > left:
            return None, f"updates.characters[{name}]: no level {level} spell slot left ({max(left, 0)}/{total})."

    add_conditions = _as_list(entry.get("add_conditions"))
    remove_conditions = _as_list(entry.get("remove_conditions"))

    def apply() -> str:
        log = []
        scalars: dict[str, Any] = {}
        if hp != character.hit_points_current:
            scalars["hit_points_current"] = hp
            log.append(f"HP {hp}/{character.hit_points_max}")
        if temp_hp is not None:
            scalars["temporary_hit_points"] = temp_hp
            log.append(f"temp HP {temp_hp}")
        if entry.get("inspiration") is not None:
            scalars["inspiration"] = bool(entry["inspiration"])
            log.append(f"inspiration {'on' if entry['inspiration'] else 'off'}")
        if entry.get("notes") is not None:
            scalars["notes"] = str(entry["notes"])
            log.append("notes updated")
        for condition in add_conditions:
            if condition not in character.conditions:
                character.conditions.append(condition)
                log.append(f"+{condition}")
        for condition in remove_conditions:
            if condition in character.conditions:
                character.conditions.remove(condition)
                log.append(f"-{condition}")
        for level, count in planned_use.items():
            character.spell_slots_used[level] = character.spell_slots_used.get(level, 0) + count
            total = character.spell_slots[level]
            log.append(f"level {level} slots {total - character.spell_slots_used[level]}/{total}")
        # Always go through storage so the change is stamped, saved and pushed to the phone.
        storage.update_character(str(character.id), **scalars)
        return f"{name}: {', '.join(log) if log else 'no change'}"

    return apply, None
