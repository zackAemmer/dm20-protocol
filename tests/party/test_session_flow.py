"""Session flow helpers: live dice on phones, timing, pictures by name, appended art,
short-name lookups, items in updates, full monster stat blocks, one-call start and end."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import zlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dm20_protocol import campaign_files, main
from dm20_protocol.models import AbilityScore, Character, CharacterClass, Item, NPC, Quest, Race
from dm20_protocol.party import timing
from dm20_protocol.party.queue import ActionQueue, ResponseQueue
from dm20_protocol.rulebooks.models import MonsterAction, MonsterDefinition
from dm20_protocol.storage import DnDStorage, loose_name_match


def _fn(tool):
    return getattr(tool, "fn", tool)


def _png(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG_B64 = base64.b64encode(_png()).decode()


def _character(name: str, player: str) -> Character:
    return Character(
        name=name, player_name=player,
        character_class=CharacterClass(name="Fighter", level=3, hit_dice="1d10"),
        race=Race(name="Dwarf"),
        abilities={a: AbilityScore(score=12) for a in
                   ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")},
        hit_points_max=30, hit_points_current=30,
        inventory=[Item(name="Champion's medal"), Item(name="Javelin", quantity=4)],
    )


@pytest.fixture
def store(tmp_path):
    storage = DnDStorage(data_dir=str(tmp_path / "data"))
    storage.create_campaign("The Fairest Man", "desc")
    storage.add_character(_character("Sister Clementine", "P4"))
    storage.add_character(_character("Grimble Crumbbeard", "P2"))
    storage.add_npc(NPC(name="Sir Rafe Galloway"))
    storage.add_quest(Quest(title="Rescue Rafe", description="Bring him back."))
    return storage


def _campaign_dir(storage):
    return storage._split_backend._get_campaign_dir(storage.get_current_campaign().name)


def _server(storage, loop=None):
    campaign_dir = _campaign_dir(storage)
    ids = [c.id for c in storage.list_characters_detailed()]
    return SimpleNamespace(
        campaign_dir=campaign_dir,
        response_queue=ResponseQueue(campaign_dir),
        action_queue=ActionQueue(campaign_dir),
        language_prefs=SimpleNamespace(get=lambda pid: "en"),
        languages_in_use=lambda: {pid: "en" for pid in ids},
        token_manager=SimpleNamespace(get_all_tokens=lambda: {**{pid: "t" for pid in ids}, "OBSERVER": "o"}),
        connection_manager=SimpleNamespace(broadcast=lambda message: message),
        _display_name=lambda pid: pid,
        mark_dm_active=lambda: None,
        _loop=loop,
    )


def _call(tool, server, storage, **kwargs):
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"), patch.object(main, "storage", storage):
        out = _fn(tool)(**kwargs)
        return asyncio.run(out) if asyncio.iscoroutine(out) else out


# ----- dice on phones -----

def _capture_broadcasts():
    sent = []

    def fake(coro_or_message, loop):
        sent.append(coro_or_message)

    return sent, fake


def test_rolls_are_shown_on_phones_unless_hidden(store):
    loop = MagicMock()
    loop.is_closed.return_value = False
    server = _server(store, loop)
    sent, fake = _capture_broadcasts()
    with patch("asyncio.run_coroutine_threadsafe", fake):
        _call(main.roll_dice, server, store, rolls=[
            {"dice": "1d20+3", "label": "Tansy Deception check"},
            {"dice": "1d20", "label": "Guard Perception (secret)", "hidden": True},
            {"dice": "banana", "label": "broken"},
        ])
        _call(main.roll_dice, server, store, dice_notation="1d20", label="Secret", hidden=True)
    assert len(sent) == 1
    rolls = sent[0]["rolls"]
    assert [r["label"] for r in rolls] == ["Tansy Deception check"]
    assert rolls[0]["dice"] == "1d20+3" and isinstance(rolls[0]["total"], int)
    assert sent[0]["type"] == "rolls"


def test_roll_works_when_party_mode_is_off(store):
    out = _call(main.roll_dice, None, store, dice_notation="1d6", label="A")
    assert out.startswith("🎲 A")


# ----- timing -----

def test_moment_is_timed_from_collect_to_send(store):
    server = _server(store)
    _call(main.party_collect_actions, server, store)
    timing.count_call()  # a roll_dice call in between
    out = _call(main.party_push, server, store, narrative="The fog lifts.")
    assert "Moment:" in out and "tool calls" in out
    record = json.loads((_campaign_dir(store) / "party" / "timing.jsonl").read_text().splitlines()[-1])
    assert record["tool_calls"] >= 2 and record["narrative_chars"] == len("The fog lifts.")
    # A push with no collect before it is not a timed moment.
    assert "Moment:" not in _call(main.party_push, server, store, narrative="Later.")
    summary = _call(main.party_timing, server, store)
    assert "Moments timed: 1" in summary and "median" in summary


def test_timing_summary_without_data(store):
    assert timing.summary(_campaign_dir(store)) == "No moments timed yet."


# ----- pictures by name, appended art -----

def test_picture_is_saved_once_and_sent_by_name(store):
    server = _server(store)
    out = _call(main.save_campaign_picture, server, store, name="portrait_rafe", image_base64=PNG_B64)
    assert "Saved picture 'portrait_rafe'" in out
    out = _call(main.party_push, server, store, narrative="He smiles.", image_name="portrait_rafe",
                image_caption="Sir Rafe")
    assert "Pushed to connected players" in out
    record = server.response_queue.get_all()[-1]
    assert record["image"]["caption"] == "Sir Rafe" and record["image"]["file"].startswith("img_")

    missing = _call(main.party_push, server, store, narrative="x", image_name="nobody")
    assert missing.startswith("Not sent") and "portrait_rafe" in missing
    assert _call(main.party_push, server, store, image_name="../etc").startswith("Not sent")


def test_sending_base64_with_a_name_keeps_it_for_next_time(store):
    server = _server(store)
    _call(main.party_push, server, store, narrative="Dusk.", image_base64=PNG_B64, image_name="sunset")
    assert campaign_files.list_pictures(_campaign_dir(store)) == ["sunset"]
    assert "Pushed" in _call(main.party_push, server, store, image_name="sunset")


def test_save_picture_rejects_non_png(store):
    out = _call(main.save_campaign_picture, None, store, name="x", image_base64=base64.b64encode(b"GIF89a").decode())
    assert out.startswith("Error") and "PNG" in out


def test_art_append_adds_only_new_text(store):
    assert "Created" in _call(main.write_campaign_art, None, store, content='scene("a", {})\n')
    out = _call(main.write_campaign_art, None, store, content='scene("b", {})', append=True)
    assert "Appended" in out
    text = _call(main.read_campaign_art, None, store)
    assert text == 'scene("a", {})\n\nscene("b", {})\n'
    again = _call(main.write_campaign_art, None, store, content='scene("b", {})', append=True)
    assert again.startswith("Not changed")
    assert _call(main.write_campaign_art, None, store, content="x", name="other.px.js", append=True).startswith("Error")


# ----- short names -----

def test_loose_name_match_is_unique_or_nothing():
    names = ["Sister Clementine", "Grimble Crumbbeard", "Sir Rafe Galloway", "Sir Fluffington"]
    assert loose_name_match("clementine", names) == "Sister Clementine"
    assert loose_name_match("Grim", names) == "Grimble Crumbbeard"
    assert loose_name_match("Rafe", names) == "Sir Rafe Galloway"
    assert loose_name_match("Sir", names) is None          # two fit
    assert loose_name_match("Bru", names) is None
    assert loose_name_match("", names) is None


def test_short_names_find_characters_and_npcs_but_never_delete(store):
    assert store.get_character("Clementine").name == "Sister Clementine"
    assert store.get_character("grimble").name == "Grimble Crumbbeard"
    assert store.get_npc("Rafe").name == "Sir Rafe Galloway"
    store.update_character("Clementine", hit_points_current=11)
    assert store.get_character("Sister Clementine").hit_points_current == 11
    assert store.find_character("Clementine") is None      # exact lookups stay exact
    store.remove_character("Clementine")
    assert store.get_character("Sister Clementine") is not None


def test_private_message_can_use_a_short_name(store):
    server = _server(store)
    out = _call(main.party_push, server, store, private_messages='{"Clementine": "Only you see it."}')
    assert "Pushed" in out
    assert list(server.response_queue.get_all()[-1]["private"]) == [store.get_character("Clementine").id]


# ----- items in updates -----

def test_updates_can_give_and_take_items(store):
    server = _server(store)
    out = _call(main.party_push, server, store, narrative="The trade is made.", updates=json.dumps({"characters": [
        {"name": "Grimble", "remove_items": ["Champion's medal", {"name": "javelin", "quantity": 1}]},
        {"name": "Clementine", "add_items": [{"name": "Copper pieces", "quantity": 50, "item_type": "treasure"}]},
    ]}))
    assert "Saved:" in out
    grimble = {i.name: i.quantity for i in store.get_character("Grimble").inventory}
    assert grimble == {"Javelin": 3}
    assert any(i.name == "Copper pieces" and i.quantity == 50 for i in store.get_character("Clementine").inventory)


@pytest.mark.parametrize("entry,reason", [
    ({"remove_items": ["Crown"]}, "no 'Crown' in the inventory"),
    ({"remove_items": [{"name": "Javelin", "quantity": 9}]}, "only 4x Javelin"),
    ({"add_items": [{"name": "Rope", "quantity": 0}]}, "not valid"),
    ({"add_items": [{"name": "Rope", "colour": "red"}]}, "unknown key"),
    ({"add_items": "Rope"}, "must be a list"),
])
def test_bad_item_updates_change_nothing(store, entry, reason):
    server = _server(store)
    out = _call(main.party_push, server, store, narrative="x",
                updates=json.dumps({"characters": [{"name": "Grimble", "hp_change": -5, **entry}]}))
    assert out.startswith("Not sent") and reason in out
    grimble = store.get_character("Grimble")
    assert grimble.hit_points_current == 30 and len(grimble.inventory) == 2


# ----- monster stat block -----

def test_monster_block_lists_every_save_and_the_actions():
    monster = MonsterDefinition(
        index="bandit-captain", name="Bandit Captain", source="srd-2024", size="Medium", type="humanoid",
        alignment="neutral", armor_class=[{"type": "armor", "value": 15}], hit_points=52, hit_dice="8d8+16",
        speed={"walk": "30 ft."}, strength=15, dexterity=16, constitution=14, intelligence=14, wisdom=11,
        charisma=14, challenge_rating=2, xp=450, proficiency_bonus=2,
        proficiencies=[
            {"value": 4, "proficiency": {"index": "saving-throw-str", "name": "Saving Throw: STR"}},
            {"value": 4, "proficiency": {"index": "skill-athletics", "name": "Skill: Athletics"}},
        ],
        actions=[MonsterAction(name="Scimitar", desc="Melee attack.", attack_bonus=5)],
        reactions=[MonsterAction(name="Parry", desc="Adds 2 to AC.")],
    )
    block = main._monster_stat_block(monster)
    assert "**Saving Throws:** STR +4*, DEX +3, CON +2, INT +2, WIS +0, CHA +2" in block
    assert "**Skills:** Athletics +4" in block
    assert "**Scimitar (+5 to hit).** Melee attack." in block and "## Reactions" in block


# ----- one-call session start and end -----

def test_session_start_returns_everything_and_starts_nothing_twice(store):
    campaign_dir = _campaign_dir(store)
    (campaign_dir / campaign_files.ARC_FILENAME).write_text("The mayor spent the money.", encoding="utf-8")
    _call(main.write_campaign_art, None, store, content='sprite("_part", "k")\nscene("fair_green", {})\n')
    _call(main.save_campaign_picture, None, store, name="fair_green", image_base64=PNG_B64)
    _call(main.add_session_note, None, store, session_number=1, title="Pie", summary="A long and full summary. " * 20)

    async def fake_rulebook(source, version):
        return f"loaded {source} {version}"

    with patch.object(main.load_rulebook, "fn", fake_rulebook, create=True), \
         patch.object(main.start_party_mode, "fn", lambda: "LINKS", create=True):
        out = _call(main.session_start, None, store, campaign="The Fairest Man")
    assert "# Session start: The Fairest Man" in out
    assert "The mayor spent the money." in out and "DM EYES ONLY" in out
    assert "Session 1, Pie" in out and out.count("A long and full summary.") == 20   # not truncated
    assert '"name": "Sister Clementine"' in out and "Rescue Rafe" in out
    assert "Saved pictures (show with image_name): fair_green" in out
    assert "Art source assets (read_campaign_art for the code): fair_green" in out
    assert "loaded srd 2024" in out and "LINKS" in out

    # Resumed chat: Party Mode already running, nothing restarted.
    server = _server(store)
    with patch.object(main.load_rulebook, "fn", fake_rulebook, create=True), \
         patch.object(main.start_party_mode, "fn", lambda: pytest.fail("restarted"), create=True):
        again = _call(main.session_start, server, store)
    assert "Already running" in again

    assert _call(main.session_start, None, store, campaign="Nope").startswith("Not started")

def test_session_end_saves_everything_in_order(store):
    server = _server(store)
    stopped = []
    with patch.object(main.backup_campaigns, "fn", lambda message="": f"Backup complete. pushed ({message})", create=True), \
         patch.object(main.stop_party_mode, "fn", lambda: stopped.append(1) or "Party Mode server stopped.", create=True):
        out = _call(main.session_end, server, store, session_number=1, title="Pie", summary="They won.",
                    quests='{"Rescue Rafe": "completed"}', game_state_notes="All done.",
                    current_location="Hollyhock Green", farewell="Good night.")
    assert "Added session note for Session 1" in out and "Quest 'Rescue Rafe': completed" in out
    assert "Backup complete. pushed (Session 1 end)" in out and "stop_service" in out
    assert stopped == [1]
    assert store.get_quest("Rescue Rafe").status == "completed"
    state = store.get_game_state()
    assert state.notes == "All done." and state.current_location == "Hollyhock Green"
    assert server.response_queue.get_all()[-1]["narrative"] == "Good night."
    assert store.get_events()[-1].title == "Session 1 end"


def test_session_end_refuses_bad_input_and_warns_on_failed_backup(store):
    server = _server(store)
    bad = _call(main.session_end, server, store, session_number=1, summary="x", quests='{"Nope": "completed"}')
    assert bad.startswith("Not saved") and store.get_sessions() == []
    bad = _call(main.session_end, server, store, session_number=1, summary="x", quests='{"Rescue Rafe": "done"}')
    assert bad.startswith("Not saved")

    with patch.object(main.backup_campaigns, "fn", lambda message="": "push failed", create=True), \
         patch.object(main.stop_party_mode, "fn", lambda: "stopped", create=True):
        out = _call(main.session_end, server, store, session_number=2, summary="Mid-session save.", stop_party=False)
    assert "WARNING: the backup did not complete" in out and "left running" in out
