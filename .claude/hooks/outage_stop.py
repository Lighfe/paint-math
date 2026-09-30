#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SubagentStop hook: mark a launch receipt as "stopped by outage" (spec 5.3, 5.7).

Claude Code runs this hook when a subagent ends. When auto mode denied a call
of that subagent without a classifier verdict, `not_started.py` (the
PermissionDenied hook) wrote an evidence file `<git dir>/agent-graph-kit-outage/<agent_id>`.
Without such a file the hook ends at once: no transcript read, no `gh` call.

With evidence, the hook reads and deletes the file, reads the launch line
`ROLE=<role> ISSUE=<n>` from the first user message of the agent's transcript
(the guard's launch line rule) and checks that `agent_type` has that role. It
holds the guard's lock from reading the issue until the comment is posted,
within the guard's overall deadline, and posts `## Launch stopped by outage: <role> (…)`
when the newest receipt of that role is still pending (`issue_state.outage_stop_comment`).

The hook prints nothing to stdout and always exits 0; errors only go to stderr.
It never outputs `decision` or `block`: a SubagentStop hook that blocks keeps the
agent running. For the same reason its command ends with `|| true`. Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import guard
import issue_state
import not_started


def _content_text(entry: dict) -> str | None:
    """The text of a transcript user entry: a string content, or its text blocks joined."""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts = [b["text"] for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n".join(parts)
    return content if isinstance(content, str) else None


def read_launch(path: str) -> tuple[str, int] | None:
    """(role, issue) of the one launch line in the first user message of a JSONL transcript,
    or None when the file is missing, is not JSONL, or that message has no single launch line."""
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                if not raw.strip():
                    continue
                entry = json.loads(raw)
                if not isinstance(entry, dict):
                    return None
                if entry.get("type") == "user":
                    text = _content_text(entry)
                    return guard._launch(text, "transcript") if text is not None else None
    except (OSError, ValueError, guard.Deny):
        return None
    return None


def handle(event, read_issue, post_comment, lock=contextlib.nullcontext, evidence_dir=None) -> None:
    """Post the stop comment for this ended subagent, if it applies. I/O errors of reading the
    issue and posting propagate. `evidence_dir()` gives the evidence folder."""
    if not isinstance(event, dict) or evidence_dir is None:
        return
    agent_id = event.get("agent_id")
    if not_started.evidence_file(Path("."), agent_id) is None:
        return
    path = not_started.evidence_file(Path(evidence_dir()), agent_id)
    try:
        reason = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    finally:
        path.unlink(missing_ok=True)  # evidence counts for one round only
    agent_type = event.get("agent_type")
    role = guard.AGENT_ROLE.get(agent_type) if isinstance(agent_type, str) else None
    transcript = event.get("agent_transcript_path")
    if role is None or not isinstance(transcript, str):
        return
    found = read_launch(transcript)
    if found is None or found[0] != role:
        return
    number = found[1]
    with lock():
        iss = read_issue(number)
        if iss.number != number:
            return
        body = issue_state.outage_stop_comment(iss, role, reason)
        if body:
            post_comment(number, body)


def main(stdin=sys.stdin, stdout=sys.stdout) -> int:
    """Always returns 0 and never writes to stdout."""
    try:
        io = guard._IO(guard._deadline_seconds())
        with guard._alarm(io):
            event = json.load(stdin)
            handle(event, io.read_issue, io.post_comment, lock=io.lock,
                   evidence_dir=lambda: io.git_dir() / not_started.EVIDENCE_DIR)
    except BaseException as e:  # nothing may reach stdout, and the exit code stays 0
        sys.stderr.write(f"outage_stop hook: {type(e).__name__}: {guard._first_line(str(e))}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
