from __future__ import annotations

import json

from conftest import FakeProvider, reply

from braeburn.engine import Engine
from braeburn.model import AssistantTurn, ContextNote, ToolCall, ToolResults, UserTurn
from braeburn.prompt import UserMessage, persona_path, to_messages


def test_chat_turn_is_saved_and_returned(cfg):
    provider = FakeProvider([reply("Hello, Sam.")])
    session = Engine(cfg, provider=provider).start_session(speaker="Sam", frontend="terminal")

    result = session.send("Sam", "hi")

    assert result.text == "Hello, Sam."
    assert [type(e) for e in session.log.entries] == [ContextNote, UserTurn, AssistantTurn]
    request = provider.requests[0]
    assert "<persona>" in request.system and "You are Braeburn." in request.system
    assert request.tools[0].name == "roll"


def test_persona_is_seeded_into_state(cfg):
    Engine(cfg, provider=FakeProvider([])).start_session(speaker="Sam", frontend="terminal")
    assert persona_path(cfg).read_text(encoding="utf-8").startswith("You are Braeburn.")


def test_session_keeps_its_system_snapshot_when_persona_changes(cfg):
    engine = Engine(cfg, provider=FakeProvider([reply("one"), reply("two")]))
    session = engine.start_session(speaker="Sam", frontend="terminal")
    session.send("Sam", "hi")
    original = session.log.system

    persona_path(cfg).write_text("You are Braeburn, now with opinions.", encoding="utf-8")
    resumed = engine.resume_session(speaker="Sam")
    resumed.send("Sam", "still there?")

    assert engine.provider.requests[-1].system == original
    assert "now with opinions" in engine.start_session(speaker="Sam", frontend="terminal").log.system


def test_resume_restores_the_transcript(cfg):
    engine = Engine(cfg, provider=FakeProvider([reply("first"), reply("second")]))
    session = engine.start_session(speaker="Sam", frontend="terminal")
    session.send("Sam", "hi")
    session.end()

    resumed = engine.resume_session(speaker="Alex")
    resumed.send("Alex", "what did Sam say?")

    texts = [e.text for e in engine.provider.requests[-1].transcript if isinstance(e, UserTurn)]
    assert texts == ["hi", "what did Sam say?"]
    assert resumed.id == session.id


def test_tool_calls_run_and_loop(cfg):
    call = ToolCall("call_1", "roll", {"dice": "2d6"})
    provider = FakeProvider([reply("Rolling.", [call], stop="tool_use"), reply("A seven!")])
    session = Engine(cfg, provider=provider).start_session(speaker="Sam", frontend="terminal")

    result = session.send("Sam", "roll for me")

    assert result.text == "Rolling.\n\nA seven!"
    results = next(e for e in session.log.entries if isinstance(e, ToolResults)).results
    assert results[0].call_id == "call_1" and not results[0].is_error
    assert results[0].output.startswith("2d6: [")
    assert len(provider.requests) == 2


def test_cut_off_tool_calls_still_get_results(cfg):
    call = ToolCall("call_1", "roll", {"dice": "d2"})
    provider = FakeProvider([reply("", [call], stop="max_tokens")])
    session = Engine(cfg, provider=provider).start_session(speaker="Sam", frontend="terminal")

    session.send("Sam", "roll")

    results = session.log.entries[-1]
    assert isinstance(results, ToolResults) and results.results[0].is_error
    assert len(provider.requests) == 1


def test_refusal_is_logged_but_left_out_of_the_next_request(cfg):
    provider = FakeProvider([reply("partial", stop="refusal"), reply("ok")])
    session = Engine(cfg, provider=provider).start_session(speaker="Sam", frontend="terminal")

    first = session.send("Sam", "something")
    session.send("Sam", "something else")

    assert first.refused
    messages = to_messages(provider.requests[-1].transcript)
    assert len(messages) == 1 and isinstance(messages[0], UserMessage)
    assert any("safety filter" in text for text in messages[0].texts)


def test_ledger_records_each_call(cfg):
    session = Engine(cfg, provider=FakeProvider([reply("hi", cost=0.02)])).start_session(
        speaker="Sam", frontend="terminal"
    )
    session.send("Sam", "hi")

    lines = (cfg.state_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert record["session"] == session.id and record["cost_usd"] == 0.02


# Participants


def test_each_speaker_is_tagged_with_their_participant_id(cfg):
    engine = Engine(cfg, provider=FakeProvider([reply("hi both")]))
    session = engine.start_session(speaker="Sam", frontend="terminal")
    session.hear("Sam", "hello")
    session.hear("Alex", "hey")
    session.respond()

    turns = [e for e in session.log.entries if isinstance(e, UserTurn)]
    assert [(t.speaker, t.speaker_id) for t in turns] == [("Sam", "sam"), ("Alex", "alex")]
    assert set(session.present) == {"sam", "alex"}
    assert len(engine.provider.requests) == 1


def test_joining_shows_notes_once(cfg):
    engine = Engine(cfg, provider=FakeProvider([reply("a"), reply("b")]))
    engine.memory.create("/memories/people/alex.md", "Keeps two lab rats, Pip and Juniper.")
    session = engine.start_session(speaker="Sam", frontend="terminal")

    session.send("Alex", "hi")
    session.send("Alex", "again")

    joins = [e for e in session.log.entries if isinstance(e, ContextNote) and "joined" in e.text]
    assert len(joins) == 1 and "Pip and Juniper" in joins[0].text


def test_session_start_briefs_on_memory(cfg):
    engine = Engine(cfg, provider=FakeProvider([]))
    engine.memory.create("/memories/people/sam.md", "Favorite apple: Honeycrisp.")

    note = engine.start_session(speaker="Sam", frontend="terminal").log.entries[0].text

    assert note.startswith("A new session started") and "Present: Sam." in note
    assert "Your memory:\n/memories/" in note and "Favorite apple: Honeycrisp." in note


def test_tools_include_memory(cfg):
    engine = Engine(cfg, provider=FakeProvider([]))
    assert [spec.name for spec in engine.tools.specs] == ["roll", "memory"]


# Reflection


def test_reflection_writes_memory_and_is_marked(cfg):
    write = ToolCall(
        "call_1", "memory", {"command": "create", "path": "/memories/people/sam.md", "file_text": "Likes rats."}
    )
    provider = FakeProvider([reply("Hi Sam."), reply("", [write], stop="tool_use"), reply("Saved Sam's notes.")])
    engine = Engine(cfg, provider=provider)
    session = engine.start_session(speaker="Sam", frontend="terminal")
    session.send("Sam", "I like rats.")

    result = session.reflect()

    assert result.text == "Saved Sam's notes."
    assert engine.memory.view("/memories/people/sam.md") == "     1\tLikes rats."
    assert "/memories/people/sam.md" in provider.requests[1].transcript[-1].text
    assert not session.log.needs_reflection and session.reflect() is None
    purposes = [json.loads(line)["purpose"] for line in (cfg.state_dir / "ledger.jsonl").read_text().splitlines()]
    assert purposes == ["chat", "reflect", "reflect"]


def test_reflection_state_survives_reload(cfg):
    engine = Engine(cfg, provider=FakeProvider([reply("hi"), reply("noted")]))
    session = engine.start_session(speaker="Sam", frontend="terminal")
    session.send("Sam", "hello")
    session.reflect()

    resumed = engine.resume_session(speaker="Sam")
    assert not resumed.log.needs_reflection


def test_sessions_closed_without_reflecting_are_found(cfg):
    engine = Engine(cfg, provider=FakeProvider([reply("hi"), reply("hi"), reply("noted")]))
    abandoned = engine.start_session(speaker="Sam", frontend="terminal")
    abandoned.send("Sam", "bye, closing the window")
    finished = engine.start_session(speaker="Alex", frontend="terminal")
    finished.send("Alex", "hello")
    finished.reflect()
    engine.start_session(speaker="Kit", frontend="terminal")  # never spoke

    assert [s.id for s in engine.unreflected_sessions()] == [abandoned.id]
    assert engine.unreflected_sessions(exclude=abandoned.id) == []
