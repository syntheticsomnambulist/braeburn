"""Braeburn's long-term memory: a folder of text files under state/memories/.

The memory tool's commands mirror Anthropic's memory tool (view, create,
str_replace, insert, delete, rename), but it's an ordinary JSON-schema tool,
so any model provider can drive it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .model import ToolSpec
from .people import Participant
from .tools import Tool, ToolError

ROOT = "/memories"
MAX_FILE_CHARS = 50_000
PRELOAD_CHARS = 6_000

# Readable but not changeable through the tool. The persona becomes writable
# at milestone 4, once state/ is versioned and edits can be rolled back.
READ_ONLY = {"self/persona.md"}


class MemoryStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    # Paths. The model sees /memories/...; nothing outside root is reachable.

    def resolve(self, path: str) -> Path:
        cleaned = path.strip().replace("\\", "/").rstrip("/")
        if cleaned == ROOT:
            relative = ""
        elif cleaned.startswith(ROOT + "/"):
            relative = cleaned[len(ROOT) + 1 :]
        else:
            raise ToolError(f"Memory paths start with {ROOT}/, like {ROOT}/people/sam.md.")
        root = self.root.resolve()
        target = (root / relative).resolve()
        if target != root and not target.is_relative_to(root):
            raise ToolError(f"That path is outside {ROOT}.")
        return target

    def virtual(self, target: Path) -> str:
        relative = target.resolve().relative_to(self.root.resolve()).as_posix()
        return ROOT if relative == "." else f"{ROOT}/{relative}"

    def _relative(self, target: Path) -> str:
        return target.resolve().relative_to(self.root.resolve()).as_posix()

    def _writable(self, path: str) -> Path:
        target = self.resolve(path)
        if target == self.root.resolve():
            raise ToolError(f"{ROOT} itself can't be changed.")
        if self._relative(target) in READ_ONLY:
            raise ToolError(f"{self.virtual(target)} is read-only.")
        return target

    def _file(self, path: str, *, writable: bool = False) -> Path:
        target = self._writable(path) if writable else self.resolve(path)
        if target.is_dir():
            raise ToolError(f"{self.virtual(target)} is a directory.")
        if not target.exists():
            raise ToolError(f"{self.virtual(target)} doesn't exist.")
        return target

    def _write(self, target: Path, text: str) -> None:
        if len(text) > MAX_FILE_CHARS:
            raise ToolError(f"That's {len(text):,} characters; memory files max out at {MAX_FILE_CHARS:,}. Condense it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as e:
            raise ToolError(f"Couldn't write {self.virtual(target)}: {e.strerror}") from e

    # Commands.

    def view(self, path: str = ROOT, view_range: list[int] | None = None) -> str:
        target = self.resolve(path)
        if target.is_dir():
            return self.tree(target)
        lines = self._file(path).read_text(encoding="utf-8").splitlines()
        first, last = view_range if view_range else (1, len(lines))
        last = len(lines) if last == -1 else last
        if not 1 <= first <= max(last, 1):
            raise ToolError("view_range is [first, last], counting from line 1.")
        numbered = [f"{n:6}\t{line}" for n, line in enumerate(lines[first - 1 : last], start=first)]
        return "\n".join(numbered) if numbered else "(empty file)"

    def create(self, path: str, file_text: str) -> str:
        target = self._writable(path)
        if target.is_dir():
            raise ToolError(f"{self.virtual(target)} is a directory.")
        existed = target.exists()
        self._write(target, file_text)
        return f"{'Overwrote' if existed else 'Created'} {self.virtual(target)}."

    def str_replace(self, path: str, old_str: str, new_str: str) -> str:
        target = self._file(path, writable=True)
        text = target.read_text(encoding="utf-8")
        if not old_str:
            raise ToolError("old_str can't be empty.")
        count = text.count(old_str)
        if count == 0:
            raise ToolError(f"old_str wasn't found in {self.virtual(target)}. View the file for its exact text.")
        if count > 1:
            raise ToolError(f"old_str appears {count} times in {self.virtual(target)}. Include more text so it matches once.")
        self._write(target, text.replace(old_str, new_str, 1))
        return f"Edited {self.virtual(target)}."

    def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        target = self._file(path, writable=True)
        lines = target.read_text(encoding="utf-8").splitlines()
        if not 0 <= insert_line <= len(lines):
            raise ToolError(f"insert_line must be between 0 and {len(lines)} for {self.virtual(target)}.")
        lines[insert_line:insert_line] = insert_text.splitlines()
        self._write(target, "\n".join(lines) + "\n")
        return f"Inserted into {self.virtual(target)} after line {insert_line}."

    def delete(self, path: str) -> str:
        target = self._writable(path)
        if not target.exists():
            raise ToolError(f"{self.virtual(target)} doesn't exist.")
        if target.is_dir():
            inside = {self._relative(p) for p in target.rglob("*")}
            if inside & READ_ONLY:
                raise ToolError(f"{self.virtual(target)} contains read-only files.")
            shutil.rmtree(target)
        else:
            target.unlink()
        return f"Deleted {self.virtual(target)}."

    def rename(self, old_path: str, new_path: str) -> str:
        source = self._writable(old_path)
        destination = self._writable(new_path)
        if not source.exists():
            raise ToolError(f"{self.virtual(source)} doesn't exist.")
        if destination.exists():
            raise ToolError(f"{self.virtual(destination)} already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        return f"Renamed {self.virtual(source)} to {self.virtual(destination)}."

    # What the harness shows Braeburn without a tool call.

    def tree(self, directory: Path | None = None) -> str:
        directory = (directory or self.root).resolve()
        lines = [f"{self.virtual(directory)}/"]
        for path in sorted(directory.rglob("*")):
            parts = path.relative_to(directory).parts
            if any(part.startswith(".") for part in parts):
                continue
            indent = "  " * len(parts)
            if path.is_dir():
                lines.append(f"{indent}{path.name}/")
            else:
                count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                flag = ", read-only" if self._relative(path) in READ_ONLY else ""
                lines.append(f"{indent}{path.name} ({count} line{'s' if count != 1 else ''}{flag})")
        return "\n".join(lines)

    def _excerpt(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8").strip()
        if len(text) > PRELOAD_CHARS:
            text = text[:PRELOAD_CHARS] + f"\n[cut off here; view {self.virtual(path)} for the rest]"
        return text

    def participant_notes(self, participant: Participant) -> str:
        path = self.root / "people" / f"{participant.id}.md"
        if path.exists():
            return f"Your notes on {participant.name} ({participant.notes_path}):\n{self._excerpt(path)}"
        return f"You have no notes on {participant.name} yet. Their file would be {participant.notes_path}."

    def briefing(self, participants: list[Participant]) -> str:
        """The session-start preload: the memory index, notes on whoever is
        present, and Braeburn's notes to itself (the persona is already in
        the system prompt).
        """
        sections = [f"Your memory:\n{self.tree()}"]
        sections += [self.participant_notes(p) for p in participants]
        for path in sorted((self.root / "self").glob("*.md")):
            if self._relative(path) not in READ_ONLY:
                sections.append(f"Your notes to yourself ({self.virtual(path)}):\n{self._excerpt(path)}")
        return "\n\n".join(sections)


REQUIRED = {
    "view": (),
    "create": ("path", "file_text"),
    "str_replace": ("path", "old_str", "new_str"),
    "insert": ("path", "insert_line", "insert_text"),
    "delete": ("path",),
    "rename": ("old_path", "new_path"),
}


def memory_tool(store: MemoryStore) -> Tool:
    def handle(args: dict[str, Any]) -> str:
        command = args["command"]
        missing = [name for name in REQUIRED[command] if name not in args]
        if missing:
            raise ToolError(f"{command} needs {', '.join(missing)}.")
        if command == "view":
            return store.view(args.get("path", ROOT), args.get("view_range"))
        if command == "create":
            return store.create(args["path"], args["file_text"])
        if command == "str_replace":
            return store.str_replace(args["path"], args["old_str"], args["new_str"])
        if command == "insert":
            return store.insert(args["path"], args["insert_line"], args["insert_text"])
        if command == "delete":
            return store.delete(args["path"])
        return store.rename(args["old_path"], args["new_path"])

    spec = ToolSpec(
        name="memory",
        description=(
            f"Your long-term memory: a folder of text files under {ROOT} that lasts between "
            "conversations. Commands: view (list a directory, or show a file with line numbers), "
            "create (write a whole file), str_replace (replace text that appears exactly once), "
            "insert (add lines after a line number), delete, and rename. Keep files short, current, "
            "and organized. Revise them rather than only appending."
        ),
        schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": list(REQUIRED)},
                "path": {"type": "string", "description": f"File or directory, starting with {ROOT}"},
                "file_text": {"type": "string", "description": "create: the file's full contents"},
                "old_str": {"type": "string", "description": "str_replace: exact text to replace; must appear once"},
                "new_str": {"type": "string", "description": "str_replace: the replacement"},
                "insert_line": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "insert: the line to insert after (0 for the top)",
                },
                "insert_text": {"type": "string", "description": "insert: the text to insert"},
                "view_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "view: optional [first, last] line numbers; last can be -1 for the end",
                },
                "old_path": {"type": "string", "description": "rename: the current path"},
                "new_path": {"type": "string", "description": "rename: the new path"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )
    return Tool(spec, handle)
