"""One-call moment helpers: batch rolls, sheets with collected actions, updates saved with a narrative."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dm20_protocol import main
from dm20_protocol.models import AbilityScore, Character, CharacterClass, Race
from dm20_protocol.party.moment import plan_updates, sheet_summary
from dm20_protocol.party.queue import ActionQueue, ResponseQueue
from dm20_protocol.storage import DnDStorage


def _fn(tool):
    return getattr(tool, "fn", tool)


def _cleric() -> Character:
    return Character(
        name="Clementine",
        player_name="P4",
        character_class=CharacterClass(name="Cleric", level=3, hit_dice="1d8"),
        race=Race(name="Tiefling"),
        abilities={
            "strength": AbilityScore(score=13), "dexterity": AbilityScore(score=10),
            "constitution": AbilityScore(score=14), "intelligence": AbilityScore(score=8),
            "wisdom": AbilityScore(score=17), "charisma": AbilityScore(score=13),
        },
        hit_points_max=24, hit_points_current=24, armor_class=18,
        skill_proficiencies=["Insight", "Persuasion"],
        saving_throw_proficiencies=["WIS", "CHA"],
        spell_slots={1: 4, 2: 2}, spell_slots_used={2: 1},
        notes="Channel Divinity 2/2.",
    )


@pytest.fixture
def store(tmp_path):
    storage = DnDStorage(data_dir=str(tmp_path / "data"))
    storage.create_campaign("Test", "desc")
    storage.add_character(_cleric())
    return storage


def _server(tmp_path, storage):
    cleric = storage.get_character("Clementine")
    return SimpleNamespace(
        campaign_dir=tmp_path,
        response_queue=ResponseQueue(tmp_path),
        action_queue=ActionQueue(tmp_path),
        language_prefs=SimpleNamespace(get=lambda pid: "en"),
        languages_in_use=lambda: {cleric.id: "en"},
        token_manager=SimpleNamespace(get_all_tokens=lambda: {cleric.id: "tok", "OBSERVER": "obs"}),
        _display_name=lambda pid: "Clementine",
        mark_dm_active=lambda: None,
    )


def _call(tool, server, storage, **kwargs):
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"), patch.object(main, "storage", storage):
        return _fn(tool)(**kwargs)


# ----- batch rolls -----

def test_single_roll_still_works():
    out = _fn(main.roll_dice)(dice_notation="1d20+3", label="Tansy Deception check")
    assert re.fullmatch(r"🎲 Tansy Deception check — \*\*1d20\+3\*\* \[\d+\] \+3 = \*\*\d+\*\*", out)


def test_several_rolls_come_back_in_order_one_line_each():
    out = _fn(main.roll_dice)(rolls=[
        {"dice": "1d20+3", "label": "Tansy Deception check"},
        {"dice": "1d20", "label": "Bru Athletics check", "advantage": True},
        {"dice": "2d6+1", "label": "Trap damage"},
    ])
    lines = out.split("\n")
    assert len(lines) == 3
    assert "Tansy Deception check" in lines[0] and "Advantage" in lines[1] and "Trap damage" in lines[2]


def test_rolls_accept_json_text_and_reject_bad_input():
    assert "A check" in _fn(main.roll_dice)(rolls='[{"dice": "1d20", "label": "A check"}]')
    assert _fn(main.roll_dice)(rolls="[oops").startswith("Not rolled")
    assert _fn(main.roll_dice)(rolls=[{"label": "no dice"}]).startswith("Not rolled")
    assert _fn(main.roll_dice)().startswith("Not rolled")
    assert "Invalid dice notation" in _fn(main.roll_dice)(rolls=[{"dice": "banana"}])


# ----- sheets -----

def test_sheet_summary_has_bonuses_with_proficiency(store):
    sheet = sheet_summary(store.get_character("Clementine"))
    assert sheet["hp"] == "24/24" and sheet["ac"] == 18
    assert sheet["mods"]["WIS"] == "+3" and sheet["mods"]["INT"] == "-1"
    assert sheet["skills"]["Insight"] == "+5"       # proficient: +3 +2
    assert sheet["skills"]["Perception"] == "+3"    # not proficient
    assert sheet["saves"]["WIS"] == "+5" and sheet["saves"]["DEX"] == "+0"
    assert sheet["spell_slots"] == {"1": "4/4", "2": "1/2"}
    assert sheet["notes"] == "Channel Divinity 2/2."


def test_collect_returns_sheets_for_players_only(tmp_path, store):
    server = _server(tmp_path, store)
    out = json.loads(_call(main.party_collect_actions, server, store))
    assert [s["name"] for s in out["sheets"]] == ["Clementine"]
    assert out["actions"] == [] and out["whispers"] == []


# ----- updates saved with the narrative -----

def test_push_saves_updates_before_sending(tmp_path, store):
    server = _server(tmp_path, store)
    out = _call(main.party_push, server, store, narrative="The spell lands.", updates=json.dumps({
        "characters": [{"name": "Clementine", "hp_change": -6, "use_spell_slot": 2, "add_conditions": ["prone"]}],
        "event": {"event_type": "combat", "description": "Clementine is knocked down."},
        "game_state": {"notes": "Mid-fight."},
    }))
    assert "Pushed to connected players" in out and "Saved:" in out
    cleric = store.get_character("Clementine")
    assert cleric.hit_points_current == 18 and cleric.conditions == ["prone"]
    assert cleric.spell_slots_used[2] == 2
    assert store.get_game_state().notes == "Mid-fight."
    assert store.get_events()[-1].description == "Clementine is knocked down."
    assert len(server.response_queue.get_all()) == 1


@pytest.mark.parametrize("updates,reason", [
    ('{"characters": [{"name": "Nobody", "hp_change": -1}]}', "no character called"),
    ('{"characters": [{"name": "Clementine", "use_spell_slot": [2, 2]}]}', "no level 2 spell slot left"),
    ('{"characters": [{"name": "Clementine", "gold": 5}]}', "unknown key"),
    ('{"event": {"event_type": "party", "description": "x"}}', "event is not valid"),
    ('{"game_state": {"mood": "tense"}}', "unknown key"),
    ('{"quests": []}', "unknown key"),
    ("[oops", "not valid JSON"),
])
def test_invalid_updates_send_nothing_and_save_nothing(tmp_path, store, updates, reason):
    server = _server(tmp_path, store)
    out = _call(main.party_push, server, store, narrative="Should not go out.", updates=updates)
    assert out.startswith("Not sent") and reason in out
    assert server.response_queue.get_all() == []
    cleric = store.get_character("Clementine")
    assert cleric.hit_points_current == 24 and cleric.spell_slots_used == {2: 1}


def test_resolve_saves_updates_with_the_answer(tmp_path, store):
    server = _server(tmp_path, store)
    cleric_id = store.get_character("Clementine").id
    action_id = server.action_queue.push(cleric_id, "I heal myself")
    server.action_queue.pop()
    out = _call(main.party_resolve_action, server, store, action_id=action_id, narrative="Warm light.",
                updates='{"characters": [{"name": "Clementine", "hp": 10, "hp_change": 5, "use_spell_slot": 1}]}')
    assert "Response broadcast" in out and "HP 15/24" in out
    assert store.get_character("Clementine").spell_slots_used[1] == 1

    bad = _call(main.party_resolve_action, server, store, action_id=action_id, narrative="x",
                updates='{"characters": [{"name": "Clementine", "hp": "lots"}]}')
    assert bad.startswith("Not resolved")


def test_hp_is_clamped_and_empty_updates_are_ignored(store):
    apply, error = plan_updates(store, {"characters": [{"name": "Clementine", "hp_change": -99}]})
    assert error is None
    apply()
    assert store.get_character("Clementine").hit_points_current == 0
    assert plan_updates(store, None) == (None, None)
    assert plan_updates(store, "{}") == (None, None)
