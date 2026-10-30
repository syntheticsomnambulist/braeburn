from __future__ import annotations

import pytest

from braeburn.memory import MemoryStore, memory_tool
from braeburn.model import ToolCall
from braeburn.people import People, slug
from braeburn.tools import ToolError, ToolRegistry

# People


def test_resolve_creates_once_per_identity(tmp_path):
    people = People(tmp_path / "people.json")
    sam = people.resolve("terminal:sam", "Sam")

    assert people.resolve("terminal:sam", "Sam") is sam
    assert sam.id == "sam" and sam.kind == "human" and sam.notes_path == "/memories/people/sam.md"


def test_same_name_on_another_identity_gets_its_own_id(tmp_path):
    people = People(tmp_path / "people.json")
    people.resolve("terminal:sam", "Sam")
    assert people.resolve("discord:1234", "Sam").id == "sam-2"


def test_registry_persists(tmp_path):
    People(tmp_path / "people.json").resolve("terminal:alex", "Alex", kind="agent")
    alex = People(tmp_path / "people.json").find("terminal:alex")
    assert alex is not None and alex.kind == "agent"


def test_slug():
    assert slug("Mary Jane O'Neil") == "mary-jane-o-neil"
    assert slug("???") == "someone"


# Memory store


@pytest.fixture
def store(tmp_path) -> MemoryStore:
    return MemoryStore(tmp_path / "memories")


def test_create_view_edit_insert(store):
    store.create("/memories/people/sam.md", "# Sam\nLikes Braeburns.\n")
    store.str_replace("/memories/people/sam.md", "Braeburns", "Honeycrisp")
    store.insert("/memories/people/sam.md", 1, "Human.")

    assert store.view("/memories/people/sam.md") == "     1\t# Sam\n     2\tHuman.\n     3\tLikes Honeycrisp."
    assert store.view("/memories/people/sam.md", [2, 2]) == "     2\tHuman."


def test_tree_lists_files_and_flags_read_only(store):
    store.create("/memories/people/sam.md", "a\nb")
    (store.root / "self").mkdir()
    (store.root / "self" / "persona.md").write_text("You are Braeburn.", encoding="utf-8")

    assert store.view("/memories") == (
        "/memories/\n  people/\n    sam.md (2 lines)\n  self/\n    persona.md (1 line, read-only)"
    )


@pytest.mark.parametrize("path", ["/memories/../.env", "/etc/passwd", "people/sam.md", "/memories/people/../../x"])
def test_paths_outside_memories_are_rejected(store, path):
    with pytest.raises(ToolError):
        store.create(path, "nope")


def test_persona_is_read_only(store):
    (store.root / "self").mkdir()
    (store.root / "self" / "persona.md").write_text("You are Braeburn.", encoding="utf-8")
    for attempt in (
        lambda: store.create("/memories/self/persona.md", "x"),
        lambda: store.delete("/memories/self/persona.md"),
        lambda: store.delete("/memories/self"),
        lambda: store.rename("/memories/self/persona.md", "/memories/p.md"),
    ):
        with pytest.raises(ToolError):
            attempt()
    assert "You are Braeburn." in store.view("/memories/self/persona.md")


def test_str_replace_needs_exactly_one_match(store):
    store.create("/memories/a.md", "apple apple")
    with pytest.raises(ToolError, match="2 times"):
        store.str_replace("/memories/a.md", "apple", "pear")
    with pytest.raises(ToolError, match="wasn't found"):
        store.str_replace("/memories/a.md", "rat", "mouse")


def test_rename_and_delete(store):
    store.create("/memories/threads/au.md", "draft")
    store.rename("/memories/threads/au.md", "/memories/threads/orchard-au.md")
    store.delete("/memories/threads")
    assert store.view("/memories") == "/memories/"


def test_briefing_shows_notes_for_present_people_and_self(store, tmp_path):
    people = People(tmp_path / "people.json")
    sam, alex = people.resolve("terminal:sam", "Sam"), people.resolve("terminal:alex", "Alex")
    store.create("/memories/people/sam.md", "Allergic to cats.")
    store.create("/memories/self/opinions.md", "Rats are underrated.")

    briefing = store.briefing([sam, alex])

    assert "Your notes on Sam (/memories/people/sam.md):\nAllergic to cats." in briefing
    assert "no notes on Alex yet. Their file would be /memories/people/alex.md" in briefing
    assert "Rats are underrated." in briefing


def test_memory_tool_reports_problems_as_errors(store):
    tools = ToolRegistry([memory_tool(store)])

    missing = tools.run(ToolCall("c", "memory", {"command": "create", "path": "/memories/x.md"}))
    escape = tools.run(ToolCall("c", "memory", {"command": "view", "path": "/memories/../../"}))
    ok = tools.run(ToolCall("c", "memory", {"command": "create", "path": "/memories/x.md", "file_text": "hi"}))

    assert missing.is_error and "needs file_text" in missing.output
    assert escape.is_error and ok.output == "Created /memories/x.md."
