"""Party Mode: per-player language, read-aloud, and answering several actions at once."""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

from dm20_protocol import main
from dm20_protocol.party import bridge, lang, narration
from dm20_protocol.party.queue import ActionQueue, ResponseQueue


def _fn(tool):
    return getattr(tool, "fn", tool)


# ----- language codes and translations -----

def test_normalize_language_codes():
    assert lang.normalize("zh-CN") == "zh"
    assert lang.normalize("zh_Hans_CN") == "zh"
    assert lang.normalize("zh-TW") == "zh-Hant"
    assert lang.normalize("ZH-HANT") == "zh-Hant"
    assert lang.normalize("en-GB") == "en"
    assert lang.normalize("中文") == "zh"
    assert lang.normalize("fr") is None
    assert lang.normalize(None) is None


def test_parse_translations_accepts_text_or_object():
    parsed, err = lang.parse_translations('{"zh": "你好", "zh-TW": {"narrative": "你好嗎", "image_caption": "黃昏"}}')
    assert err is None
    assert parsed == {"zh": {"narrative": "你好"}, "zh-Hant": {"narrative": "你好嗎", "image_caption": "黃昏"}}
    assert lang.parse_translations(None) == ({}, None)
    assert lang.parse_translations({"zh": "  "}) == ({}, None)  # empty text is no translation


def test_parse_translations_rejects_bad_input():
    for bad in ("not json", "[1, 2]", '{"xx": "hi"}', '{"zh": 5}'):
        parsed, err = lang.parse_translations(bad)
        assert parsed == {} and err, bad


def test_localize_swaps_narrative_and_caption_only_when_translated():
    raw = {
        "narrative": "Fog rolls in.",
        "image": {"file": "img_1.png", "caption": "Dusk"},
        "translations": {"zh": {"narrative": "雾来了。", "image_caption": "黄昏"}},
    }
    base = {"narrative": raw["narrative"], "image": raw["image"]}
    zh = lang.localize(dict(base), raw, "zh")
    assert zh["narrative"] == "雾来了。" and zh["image"]["caption"] == "黄昏"
    assert raw["image"]["caption"] == "Dusk"  # the stored record is not changed
    assert lang.localize(dict(base), raw, "zh-Hant")["narrative"] == "雾来了。"  # same base language
    assert lang.localize(dict(base), raw, "en")["narrative"] == "Fog rolls in."
    assert lang.localize(dict(base), {"narrative": "x"}, "zh")["narrative"] == "Fog rolls in."


def test_missing_for_lists_languages_left_on_default_text():
    assert lang.missing_for({}, {"en", "zh"}, True) == ["zh"]
    assert lang.missing_for({"zh": {"narrative": "好"}}, {"en", "zh", "zh-Hant"}, True) == []
    assert lang.missing_for({}, {"en", "zh"}, False) == []  # picture only: nothing to translate


def test_language_prefs_persist(tmp_path):
    path = tmp_path / "party" / "languages.json"
    prefs = lang.LanguagePrefs(path)
    assert prefs.get("mei") == "en"
    assert prefs.set("mei", "zh-CN") == "zh"
    assert prefs.set("mei", "klingon") is None and prefs.get("mei") == "zh"
    assert lang.LanguagePrefs(path).all() == {"mei": "zh"}
    path.write_text("{broken")
    assert lang.LanguagePrefs(path).get("mei") == "en"


# ----- each player gets their own language -----

def test_bridge_and_replay_use_player_language(e2e_permission_resolver, tmp_path):
    raw = {"id": "res_1", "narrative": "Fog.", "translations": {"zh": {"narrative": "雾。"}},
           "private": {"elara": "只有你看到。"}}
    assert bridge.format_response(raw, "thorin", e2e_permission_resolver, language="en")["narrative"] == "Fog."
    out = bridge.format_response(raw, "elara", e2e_permission_resolver, language="zh")
    assert out["narrative"] == "雾。" and out["private"] == "只有你看到。"
    assert "translations" not in out  # other languages are not sent to the device

    queue = ResponseQueue(tmp_path)
    queue.push({"narrative": "Fog.", "translations": {"zh": {"narrative": "雾。"}}})
    assert queue.get_for_player("elara", language="zh")[0]["narrative"] == "雾。"
    assert queue.get_for_player("thorin", language="en")[0]["narrative"] == "Fog."
    assert queue.get_for_player("thorin")[0]["narrative"] == "Fog."


@contextmanager
def _devices(server, *tokens):
    """Open one WebSocket per token on a shared event loop, as the real server has."""
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(server.app))

        async def bind_loop():
            server._loop = asyncio.get_running_loop()  # set by _run_server in production

        client.portal.call(bind_loop)
        yield [stack.enter_context(client.websocket_connect(f"/ws?token={t}")) for t in tokens]


def _read_until(ws, kind, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no {kind} message")


def test_devices_receive_their_own_language(e2e_server, e2e_tokens):
    with _devices(e2e_server, e2e_tokens["thorin"], e2e_tokens["elara"]) as (en_ws, zh_ws):
        hello = _read_until(zh_ws, "connected")
        assert hello["language"] == "en" and "zh" in hello["languages"]
        zh_ws.send_json({"type": "set_language", "language": "zh-CN"})
        assert _read_until(zh_ws, "language")["language"] == "zh"
        assert e2e_server.languages_in_use()["elara"] == "zh"

        e2e_server.response_queue.push({
            "narrative": "The bell tolls.", "private": {}, "dm_only": "",
            "translations": {"zh": {"narrative": "钟声响起。"}},
        })
        assert _read_until(zh_ws, "narrative")["narrative"] == "钟声响起。"
        assert _read_until(en_ws, "narrative")["narrative"] == "The bell tolls."

        # Changing language and asking for history again re-sends the story in that language.
        zh_ws.send_json({"type": "set_language", "language": "en"})
        _read_until(zh_ws, "language")
        zh_ws.send_json({"type": "history_request", "since": ""})
        assert _read_until(zh_ws, "narrative")["narrative"] == "The bell tolls."

    # The choice survives a server restart (it is stored with the campaign).
    assert lang.LanguagePrefs(e2e_server.campaign_dir / "party" / "languages.json").get("elara") == "en"


# ----- read-aloud -----

def test_speakable_strips_markup_and_limits_length():
    text = '**Ines**: "Run!"\n\n[Round 2 -- Goblin] (Attack: 12 + 3 = 15) *The fog thickens.* [map](http://x)'
    spoken = narration.speakable(text)
    assert "*" not in spoken and "http" not in spoken and "Round 2 -- Goblin." in spoken
    assert len(narration.speakable("字。" * 4000)) <= narration.MAX_CHARS


def test_narrator_caches_per_message_and_language(monkeypatch):
    calls = []

    async def fake_synth(self, text, language):
        calls.append((text, language))
        return f"{language}:{text}".encode()

    monkeypatch.setattr(narration.Narrator, "_synthesize", fake_synth)
    monkeypatch.setattr(narration, "available", lambda: True)
    narrator = narration.Narrator()

    async def go():
        a = await narrator.audio_for("res_1", "Fog.", "en")
        b = await narrator.audio_for("res_1", "Fog.", "en")
        c = await narrator.audio_for("res_1", "雾。", "zh")
        return a, b, c

    a, b, c = asyncio.run(go())
    assert a == b == b"en:Fog." and c == "zh:雾。".encode()
    assert calls == [("Fog.", "en"), ("雾。", "zh")]
    assert narration.NARRATOR_VOICES["zh"].startswith("zh-CN") and narration.NARRATOR_VOICES["en"].startswith("en-")


def test_narrator_failure_returns_none(monkeypatch):
    async def boom(self, text, language):
        raise RuntimeError("service down")

    monkeypatch.setattr(narration.Narrator, "_synthesize", boom)
    monkeypatch.setattr(narration, "available", lambda: True)
    assert asyncio.run(narration.Narrator().audio_for("res_1", "Fog.", "en")) is None
    monkeypatch.setattr(narration, "available", lambda: False)
    assert asyncio.run(narration.Narrator().audio_for("res_2", "Fog.", "en")) is None


def test_speak_request_and_auto_read_aloud_use_device_language(e2e_server, e2e_tokens, monkeypatch):
    spoken = []

    async def fake_synth(self, text, language):
        spoken.append((language, text))
        return b"MP3" + language.encode()

    monkeypatch.setattr(narration.Narrator, "_synthesize", fake_synth)
    monkeypatch.setattr(narration, "available", lambda: True)

    with _devices(e2e_server, e2e_tokens["thorin"], e2e_tokens["elara"]) as (en_ws, zh_ws):
        assert _read_until(zh_ws, "connected")["read_aloud_available"] is True
        zh_ws.send_json({"type": "set_language", "language": "zh"})
        _read_until(zh_ws, "language")
        zh_ws.send_json({"type": "set_read_aloud", "on": True})   # only this device reads everything aloud
        zh_ws.send_json({"type": "heartbeat"})

        rid = e2e_server.response_queue.push({
            "narrative": "**Ines**: The bell tolls.", "private": {"elara": "只有你听见低语。"}, "dm_only": "",
            "translations": {"zh": {"narrative": "**伊内丝**:钟声响起。"}},
        })
        audio = _read_until(zh_ws, "audio")
        assert audio["language"] == "zh" and audio["response_id"] == rid and audio["format"] == "mp3"
        assert spoken == [("zh", "伊内丝:钟声响起。\n只有你听见低语。")]  # narrative plus this player's whisper

        # The English device did not ask for auto read-aloud; it taps the speaker button.
        _read_until(en_ws, "narrative")
        en_ws.send_json({"type": "speak", "response_id": rid})
        audio = _read_until(en_ws, "audio")
        assert audio["language"] == "en"
        assert spoken[-1] == ("en", "Ines: The bell tolls.")  # no whisper: it was not for this player


def test_speak_reports_when_unavailable(e2e_server, e2e_tokens, monkeypatch):
    monkeypatch.setattr(narration, "available", lambda: False)
    rid = e2e_server.response_queue.push({"narrative": "Fog.", "private": {}, "dm_only": ""})
    with _devices(e2e_server, e2e_tokens["thorin"]) as (ws,):
        assert _read_until(ws, "connected")["read_aloud_available"] is False
        ws.send_json({"type": "speak", "response_id": rid})
        assert _read_until(ws, "audio_unavailable")["response_id"] == rid


# ----- DM tools: translations, batches, waiting -----

def _tool_server(tmp_path, languages=None):
    pushed = []
    languages = languages or {}
    server = SimpleNamespace(
        campaign_dir=tmp_path,
        response_queue=ResponseQueue(tmp_path, on_push=pushed.append),
        action_queue=ActionQueue(tmp_path),
        language_prefs=SimpleNamespace(get=lambda pid: languages.get(pid, "en")),
        languages_in_use=lambda: dict(languages),
        _display_name=lambda pid: pid.title(),
        mark_dm_active=lambda: None,
    )
    return server, pushed


def _call(tool, server, **kwargs):
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = _fn(tool)(**kwargs)
        return asyncio.run(out) if asyncio.iscoroutine(out) else out


def test_push_stores_translations_and_flags_missing_ones(tmp_path):
    server, pushed = _tool_server(tmp_path, {"aldric": "en", "mei": "zh"})
    out = _call(main.party_push, server, narrative="Fog rolls in.", translations='{"zh": "雾涌来。"}')
    assert "Pushed" in out and "Note" not in out
    assert pushed[0]["translations"] == {"zh": {"narrative": "雾涌来。"}}

    out = _call(main.party_push, server, narrative="The wind drops.")
    assert "no translation for zh" in out and "Mei" in out

    out = _call(main.party_push, server, narrative="x", translations='{"de": "y"}')
    assert out.startswith("Not sent") and len(pushed) == 2


def test_pop_all_and_resolve_together(tmp_path):
    server, pushed = _tool_server(tmp_path, {"aldric": "en", "mei": "zh"})
    a1 = server.action_queue.push("aldric", "I block the door.")
    a2 = server.action_queue.push("mei", "我观察钟楼。")
    a3 = server.action_queue.push("aldric", "And I draw my sword.")

    one = json.loads(_call(main.party_pop_action, server))
    assert one["action"]["id"] == a1 and one["remaining"] == 2
    assert one["action"]["player_language"] == "en"
    assert one["languages"] == {"by_player": {"Aldric": "en", "Mei": "zh"}, "write_in": ["en", "zh"]}

    rest = json.loads(_call(main.party_pop_action, server, all_pending=True))
    assert [a["id"] for a in rest["actions"]] == [a2, a3] and rest["remaining"] == 0
    assert rest["actions"][0]["player_language"] == "zh"
    assert json.loads(_call(main.party_pop_action, server, all_pending=True)) == {"empty": True, "pending": 0}

    out = _call(main.party_resolve_action, server, action_id=f"{a2}, {a3}",
                narrative="Both at once.", translations='{"zh": "同时发生。"}')
    assert "2 actions answered together" in out
    assert pushed[0]["action_ids"] == [a2, a3] and pushed[0]["action_id"] == a2
    assert server.action_queue.get_status(a2) == server.action_queue.get_status(a3) == "resolved"
    assert server.action_queue.get_status(a1) == "processing"

    out = _call(main.party_resolve_action, server, action_id=f"{a1},act_nope", narrative="x")
    assert out.startswith("Not resolved") and "act_nope" in out
    assert server.action_queue.get_status(a1) == "processing"


def test_wait_for_actions_returns_batch_or_times_out(tmp_path):
    server, _ = _tool_server(tmp_path, {"aldric": "en"})
    empty = json.loads(_call(main.party_wait_for_actions, server, timeout_seconds=1, gather_seconds=0))
    assert empty["empty"] is True

    async def players_act_shortly():
        async def later():
            await asyncio.sleep(0.2)
            server.action_queue.push("aldric", "first")
            await asyncio.sleep(0.3)
            server.action_queue.push("mei", "second, a moment later")

        task = asyncio.create_task(later())
        with patch("dm20_protocol.party.server.get_server_instance", return_value=server):
            out = await _fn(main.party_wait_for_actions)(timeout_seconds=5, gather_seconds=1)
        await task
        return json.loads(out)

    got = asyncio.run(players_act_shortly())
    assert [a["text"] for a in got["actions"]] == ["first", "second, a moment later"]
    assert got["remaining"] == 0


def test_several_actions_are_marked_answered_on_devices(e2e_server, e2e_tokens):
    a1 = e2e_server.action_queue.push("thorin", "one")
    a2 = e2e_server.action_queue.push("elara", "two")
    with _devices(e2e_server, e2e_tokens["thorin"]) as (ws,):
        _read_until(ws, "connected")
        e2e_server.response_queue.push({
            "action_id": a1, "action_ids": [a1, a2], "narrative": "Together.", "private": {}, "dm_only": "",
        })
        msg = _read_until(ws, "narrative")
        assert msg["action_ids"] == [a1, a2]
        resolved = {_read_until(ws, "action_status")["action_id"], _read_until(ws, "action_status")["action_id"]}
        assert resolved == {a1, a2}


# ----- whispers -----

def test_whispers_come_back_first_and_alone(tmp_path):
    server, _ = _tool_server(tmp_path, {"aldric": "en", "mei": "zh"})
    public1 = server.action_queue.push("aldric", "I block the door.")
    whisper = server.action_queue.push("mei", "她在说谎吗?", private=True)
    public2 = server.action_queue.push("aldric", "I ask what she fears.")
    assert server.action_queue.pending_whispers() == 1

    got = json.loads(_call(main.party_wait_for_actions, server, timeout_seconds=2, gather_seconds=0))
    assert [a["id"] for a in got["actions"]] == [whisper] and "whisper" in got
    assert got["actions"][0]["player_language"] == "zh" and got["remaining"] == 2

    got = json.loads(_call(main.party_wait_for_actions, server, timeout_seconds=2, gather_seconds=0))
    assert [a["id"] for a in got["actions"]] == [public1, public2] and "whisper" not in got

    # party_pop_action follows the same order.
    server.action_queue.push("aldric", "public")
    w2 = server.action_queue.push("mei", "secret", private=True)
    one = json.loads(_call(main.party_pop_action, server))
    assert one["action"]["id"] == w2 and "whisper" in one


def test_whisper_reply_is_private_even_if_written_as_narrative(tmp_path):
    server, pushed = _tool_server(tmp_path, {"aldric": "en", "mei": "zh"})
    whisper = server.action_queue.push("mei", "她在说谎吗?", private=True)
    json.loads(_call(main.party_pop_action, server))

    out = _call(main.party_resolve_action, server, action_id=whisper,
                narrative="她的手在发抖。她在隐瞒什么。", translations='{"zh": "ignored"}')
    assert "Whisper answered privately" in out and "Mei" in out
    record = pushed[0]
    assert record["narrative"] == "" and "translations" not in record
    assert record["private"] == {"mei": "她的手在发抖。她在隐瞒什么。"}
    # Nobody else receives anything from it.
    assert server.response_queue.get_for_player("aldric")[0].get("narrative") == ""
    assert "private" not in server.response_queue.get_for_player("aldric")[0]
    assert server.response_queue.get_for_player("mei")[0]["private"].startswith("她的手")
    assert server.action_queue.get_status(whisper) == "resolved"


def test_whisper_cannot_be_resolved_with_public_actions(tmp_path):
    server, pushed = _tool_server(tmp_path, {"aldric": "en", "mei": "zh"})
    public = server.action_queue.push("aldric", "I block the door.")
    whisper = server.action_queue.push("mei", "secret", private=True)
    out = _call(main.party_resolve_action, server, action_id=f"{public},{whisper}", narrative="Both.")
    assert out.startswith("Not resolved") and "on its own" in out and pushed == []
    assert _call(main.party_resolve_action, server, action_id=whisper, narrative="  ").startswith("Not resolved")
