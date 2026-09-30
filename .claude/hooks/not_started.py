#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PermissionDenied hook: mark a launch receipt as "not started" (spec 5.3, 5.7).

Claude Code runs this hook when auto mode denies a tool call, also when it
denies without a classifier verdict. The guard has already posted the launch
receipt in PreToolUse, so without this hook the issue would stay pending.

Reads one PermissionDenied event on stdin. The call is classified with the
guard's rules (`guard.classify`). For a guarded launch of pm, engineer or qa
(also the Codex QA launcher and a SendMessage continuation) the hook reads the
issue and posts `## Launch not started: <role> (…)` when the newest receipt
carries the call hash of this event's tool_use_id (`issue_state.not_started_comment`).
It holds the guard's lock from reading the issue until the comment is posted,
within the guard's overall deadline.

It also records outage evidence (spec 5.3, issue #52): for a denial inside a
subagent (the event has an `agent_id`) whose reason has no classifier verdict
(`issue_state.is_no_verdict`), it writes the first line of the reason to
`<git dir>/agent-graph-kit-outage/<agent_id>`, for any tool call. The
SubagentStop hook `outage_stop.py` reads and deletes that file. A failure while
writing evidence never stops the not-started comment.

The hook cannot block anything: Claude Code ignores its exit code and stderr.
It prints nothing to stdout and always exits 0; errors only go to stderr. It
never asks Claude Code to try the call again. Stdlib only.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path

import guard
import issue_state


EVIDENCE_DIR = "agent-graph-kit-outage"  # in the git dir, next to the guard's lock file
AGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def evidence_file(folder: Path, agent_id) -> Path | None:
    """The evidence file of this agent_id in `folder`, or None when the agent_id is not a safe name."""
    if isinstance(agent_id, str) and AGENT_ID.fullmatch(agent_id):
        return Path(folder) / agent_id
    return None


def record_evidence(event: dict, evidence_dir) -> None:
    """Write the evidence file when a subagent's call was denied without a classifier verdict.
    `evidence_dir()` gives the folder; it is only called when a file is written."""
    reason = event.get("reason")
    agent_id = event.get("agent_id")
    if not issue_state.is_no_verdict(reason) or evidence_file(Path("."), agent_id) is None:
        return
    folder = Path(evidence_dir())
    folder.mkdir(mode=0o700, exist_ok=True)
    evidence_file(folder, agent_id).write_text(issue_state.evidence_line(reason))


def handle(event, read_issue, post_comment, lock=contextlib.nullcontext, evidence_dir=None) -> None:
    """Record outage evidence (if `evidence_dir` is given), then post the not-started comment for
    this denied call, if it applies. I/O errors of the comment part propagate."""
    if not isinstance(event, dict):
        return
    if evidence_dir is not None:
        try:
            record_evidence(event, evidence_dir)
        except Exception as e:  # evidence must never stop the not-started comment
            sys.stderr.write(f"not_started hook: evidence not written: {type(e).__name__}: "
                             f"{guard._first_line(str(e))}\n")
    call_hash = guard.event_call_hash(event)
    if call_hash is None:
        return
    try:
        call = guard.classify(event)
    except guard.Deny:
        return  # the guard denied this call itself, so it posted no receipt
    if call is None or call.role == "close":
        return
    reason = event.get("reason")
    reason = reason if isinstance(reason, str) and reason.strip() else "(no reason given)"
    with lock():
        iss = read_issue(call.issue)
        if iss.number != call.issue:
            return
        body = issue_state.not_started_comment(iss, call_hash, reason, role=call.role)
        if body:
            post_comment(call.issue, body)


def main(stdin=sys.stdin, stdout=sys.stdout) -> int:
    """Always returns 0 and never writes to stdout."""
    try:
        io = guard._IO(guard._deadline_seconds())
        with guard._alarm(io):
            event = json.load(stdin)
            handle(event, io.read_issue, io.post_comment, lock=io.lock,
                   evidence_dir=lambda: io.git_dir() / EVIDENCE_DIR)
    except BaseException as e:  # nothing may reach stdout, and the exit code stays 0
        sys.stderr.write(f"not_started hook: {type(e).__name__}: {guard._first_line(str(e))}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
