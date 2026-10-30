"""Terminal front-end: `braeburn chat` and `braeburn sessions`."""

from __future__ import annotations

import argparse
import json
import re
import sys

from .config import load_config
from .engine import Engine, Session
from .model import AssistantTurn, ToolCall, ToolResult, Usage, UserTurn
from .providers import ProviderError
from .store import list_sessions

HELP = """\
Commands:
  /as NAME   switch who's typing (they join the conversation if they're new)
  /who       who's in this conversation
  /memory    list Braeburn's memory files
  /cost      tokens and cost so far this session
  /help      this list
  /bye       end the session; Braeburn updates its memory first (also /quit, /exit, Ctrl+C)

Start a line with a known name and a colon (Alex: hi) to say one line as them."""

# "Alex: hello" says one line as Alex, but only if Alex is already known, so
# an ordinary "Note: ..." isn't mistaken for a speaker.
PREFIX = re.compile(r"^([^\s:/][^:]{0,39}):\s+(.+)$")


def fmt_usage(usage: Usage) -> str:
    return (
        f"[in {usage.input_tokens:,} | cache read {usage.cache_read_tokens:,} | "
        f"cache write {usage.cache_write_tokens:,} | out {usage.output_tokens:,} | ${usage.cost_usd:.4f}]"
    )


def show_text(text: str) -> None:
    print(text, end="", flush=True)


def first_line(text: str, limit: int = 100) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


def show_tool(call: ToolCall, result: ToolResult) -> None:
    if call.name == "memory":
        target = call.args.get("path") or call.args.get("old_path") or "/memories"
        what = f"memory {call.args.get('command', '?')} {target}"
    else:
        what = f"{call.name}: {first_line(result.output)}"
    failed = f" failed: {first_line(result.output)}" if result.is_error else ""
    print(f"\n  ({what}{failed})", flush=True)


def show_recent(session: Session, count: int = 2) -> None:
    """Print the last few lines of a resumed session for orientation."""
    lines = []
    for entry in session.log.entries:
        if isinstance(entry, UserTurn):
            lines.append(f"{entry.speaker}> {entry.text}")
        elif isinstance(entry, AssistantTurn) and entry.text and entry.stop != "refusal":
            lines.append(f"Braeburn: {entry.text}")
    for line in lines[-count:]:
        print(line)


def ask_name() -> str:
    while True:
        name = input("Who's here? ").strip()
        if name:
            return name


def reflect(session: Session, debug: bool) -> Usage:
    """Let Braeburn update its memory. Interrupting it just leaves the
    session to be caught up next time.
    """
    try:
        reply = session.reflect(on_tool=show_tool)
    except ProviderError as e:
        print(f"\n[error] {e} It'll catch up next time.")
        return Usage()
    except KeyboardInterrupt:
        print("\n[skipped; it'll catch up next time]")
        return Usage()
    if reply is None:
        return Usage()
    summary = first_line(reply.text, 200) or "(nothing noted)"
    print(f"\n  Braeburn noted: {summary}")
    if debug:
        print(fmt_usage(reply.usage))
    return reply.usage


def catch_up(engine: Engine, exclude: str | None, debug: bool) -> None:
    pending = engine.unreflected_sessions(exclude=exclude)
    if not pending:
        return
    plural = "s" if len(pending) > 1 else ""
    print(f"Braeburn is catching up on {len(pending)} earlier conversation{plural}…")
    for session in pending:
        reflect(session, debug)
    print()


def run_chat(args: argparse.Namespace) -> int:
    cfg = load_config()
    try:
        engine = Engine(cfg)
    except ProviderError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    try:
        speaker = args.speaker or ask_name()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0

    if args.resume:
        try:
            session = engine.resume_session(
                speaker=speaker, session_id=None if args.resume == "latest" else args.resume
            )
        except LookupError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
        catch_up(engine, exclude=session.id, debug=args.debug)
        print(f"Resumed session {session.id}.")
        show_recent(session)
    else:
        catch_up(engine, exclude=None, debug=args.debug)
        session = engine.start_session(speaker=speaker, frontend="terminal")
        print(f"Session {session.id}. Type /help for commands.")

    total = Usage()
    while True:
        try:
            line = input(f"{speaker}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            line = "/bye"
        if not line:
            continue

        if line.startswith("/"):
            command, _, rest = line.partition(" ")
            command, rest = command.lower(), rest.strip()
            if command in ("/bye", "/quit", "/exit"):
                if session.log.needs_reflection:
                    print("Braeburn is updating its memory…")
                    total += reflect(session, args.debug)
                session.end()
                print(f"Bye. This session cost ${total.cost_usd:.4f}.")
                return 0
            if command == "/as":
                if not rest:
                    print("Usage: /as NAME")
                    continue
                speaker = session.join(rest).name if rest.lower() != speaker.lower() else speaker
                print(f"Now typing as {speaker}.")
            elif command == "/who":
                print("Present: " + ", ".join(p.name for p in session.present.values()))
            elif command == "/memory":
                print(engine.memory.tree())
            elif command == "/help":
                print(HELP)
            elif command == "/cost":
                print(fmt_usage(total))
            else:
                print(f"Unknown command {command}. Type /help.")
            continue

        who, text = speaker, line
        match = PREFIX.match(line)
        if match and engine.people.find(f"terminal:{match[1].strip().lower()}"):
            who, text = match[1].strip(), match[2]

        print("Braeburn: ", end="", flush=True)
        try:
            reply = session.send(who, text, on_text=show_text, on_tool=show_tool)
        except ProviderError as e:
            hint = " Try again in a moment." if e.retryable else ""
            print(f"\n[error] {e}{hint}")
            continue
        except KeyboardInterrupt:
            print("\n[interrupted; that reply wasn't saved]")
            continue
        print()
        if reply.refused:
            print("[that reply was stopped by a safety filter]")
        total += reply.usage
        if args.debug:
            print(fmt_usage(reply.usage))


def run_sessions(args: argparse.Namespace) -> int:
    cfg = load_config()
    paths = list_sessions(cfg.state_dir / "sessions")[: args.limit]
    if not paths:
        print("No sessions yet.")
        return 0
    for path in paths:
        with path.open(encoding="utf-8") as f:
            header = json.loads(f.readline())
            first = next((json.loads(line) for line in f if '"kind": "user"' in line), None)
        preview = f"{first['speaker']}: {first['text'][:60]}" if first else "(no messages)"
        print(f"{header['id']}  {header.get('frontend', '?'):<9} {preview}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="braeburn", description="Talk to Braeburn, a persistent persona agent.")
    commands = parser.add_subparsers(dest="command", required=True)

    chat = commands.add_parser("chat", help="start or resume a terminal chat")
    chat.add_argument("--as", dest="speaker", metavar="NAME", help="your name (asked for if omitted)")
    chat.add_argument(
        "--resume", nargs="?", const="latest", metavar="ID", help="resume the latest session, or the one with this id"
    )
    chat.add_argument("--debug", action="store_true", help="show tokens and cost after each reply")
    chat.set_defaults(run=run_chat)

    sessions = commands.add_parser("sessions", help="list recent sessions")
    sessions.add_argument("--limit", type=int, default=10, help="how many to show (default 10)")
    sessions.set_defaults(run=run_sessions)

    args = parser.parse_args(argv)
    return args.run(args)
