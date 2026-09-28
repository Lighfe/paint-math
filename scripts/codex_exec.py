"""Shared runner for `codex exec` (used by qa-codex, Task 6, and the review skill, Task 8).

Flags, error texts and regexes come from spike S2 (docs/research/spike-codex-cli.md:
error table and "Consequences for the plan", #6). Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexRun:
    status: str  # "ok" | "transient" | "timeout" | "invalid_output" | "unavailable" | "unknown"
    output: dict | None
    reason: str


# Keep the user config, AGENTS.md, skills, hooks and apps out of the run, report errors as
# JSON events on stdout, and end a network outage after a few minutes (S2 Q5, Q8).
BASE_FLAGS: list[str] = [
    "--json", "--ephemeral", "--ignore-user-config",
    "--disable", "hooks", "--disable", "apps", "--disable", "unbounded_connection_retries",
    "-c", "project_doc_max_bytes=0", "-c", "skills.include_instructions=false",
]

# S2 error table, rows E1-E9 in order; the first match wins, no match is "unknown" (E10).
FAILURE_PATTERNS: list[tuple[str, re.Pattern]] = [(status, re.compile(rx, re.MULTILINE)) for status, rx in [
    ("unavailable", r"codex'?: (?:(?:command )?not found|No such file or directory)|No such file or directory: 'codex'"),
    ("unavailable", r"^(?:ERROR: )?(?:unexpected status 401 Unauthorized|Not logged in$)"),
    ("unavailable", r"^(?:ERROR: )?(?:You've hit your usage limit|You hit your spend cap|Your workspace is out of credits"
                    r"|Quota exceeded\. Check your plan|To use Codex with your ChatGPT plan)"),
    ("transient", r"^(?:ERROR: )?exceeded retry limit, last status: 429"),
    ("transient", r"^(?:ERROR: )?rate limit exceeded: "),
    ("transient", r"^(?:ERROR: )?We're currently experiencing high demand"),
    ("transient", r"^(?:ERROR: )?Selected model is at capacity"),
    ("transient", r"^(?:ERROR: )?(?:unexpected status 5\d\d|exceeded retry limit, last status: 5\d\d)"),
    ("transient", r"^(?:ERROR: )?(?:Connection failed: |stream disconnected before completion: )"),
]]

# Spec 6.2 lists a crashed process as transient. The S2 table has no crash row, so a crash is
# checked after the table: a death by signal (the npm wrapper `codex.js` re-raises the signal of
# the native binary, so Popen sees a negative code; a shell reports 128 + n), or a Rust panic line.
PANIC_PATTERN = re.compile(r"^thread '[^'\n]*' panicked at.*$", re.MULTILINE)
SIGNAL_MAX = 64

REASON_MAX = 500

# Redaction (spec P5): every comment is redacted right before it is posted, and every reason is
# redacted before it is cut to REASON_MAX (a cut secret no longer matches). Over-redaction is fine.
REDACTED = "[redacted]"
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)
SECRET_MIN = 8
SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [(re.compile(rx, re.IGNORECASE), repl) for rx, repl in [
    (r"(authorization:)[^\n]*", r"\1 " + REDACTED),
    (r"\b(bearer)[ \t]+\S{8,}", r"\1 " + REDACTED),
    (r"gh[pousr]_[a-z0-9]{20,}", REDACTED),
    (r"github_pat_[a-z0-9_]{20,}", REDACTED),
    (r"sk-[a-z0-9_-]{20,}", REDACTED),
    (r"((?:token|key|password)=)[^\s&\"'`]+", r"\1" + REDACTED),
]]


def _secret_values(environ) -> list[str]:
    """Values of secret-looking env vars (and their whitespace-joined, JSON- and repr-escaped
    forms, and each stripped line of a multi-line value), at least SECRET_MIN characters, longest
    first, so a longer form is always replaced before a shorter one."""
    values: set[str] = set()
    for name, value in environ.items():
        if not SECRET_NAME.search(name) or len(value) < SECRET_MIN:
            continue
        lines = [line.strip() for line in value.splitlines()]
        for form in (value, " ".join(value.split()), json.dumps(value)[1:-1], repr(value)[1:-1], *lines):
            if len(form) >= SECRET_MIN:
                values.add(form)
    return sorted(values, key=len, reverse=True)


def redact(text: str, environ=None) -> str:
    """Replace secret-looking env values and common credential patterns with [redacted]."""
    for value in _secret_values(os.environ if environ is None else environ):
        text = text.replace(value, REDACTED)
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _turn_failed(stdout: str) -> str | None:
    """Message of the last `turn.failed` event on stdout (`--json`), or None."""
    message = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
    return message


def failure_text(stdout: str, stderr: str) -> str:
    """Message of the last turn.failed event, else stderr."""
    message = _turn_failed(stdout)
    return message if message is not None else stderr


def _table_status(text: str) -> str | None:
    for status, pattern in FAILURE_PATTERNS:
        if pattern.search(text):
            return status
    return None


def _signal_number(returncode: int) -> int | None:
    if returncode < 0:
        return -returncode
    if 128 < returncode <= 128 + SIGNAL_MAX:
        return returncode - 128
    return None


def _crash(returncode: int, text: str) -> str | None:
    """"codex crashed (panic): <panic line>" or "codex crashed (signal <NAME>)", or None."""
    panic = PANIC_PATTERN.search(text)
    if panic:
        return "codex crashed (panic): " + _one_line(panic.group(0))
    number = _signal_number(returncode)
    if number is None:
        return None
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = str(number)
    return f"codex crashed (signal {name})"


def classify_failure(returncode: int, text: str) -> str:
    """"transient" | "unavailable" | "unknown": the S2 error table first (first match wins), then a
    crashed process (spec 6.2: transient), else unknown."""
    status = _table_status(text)
    if status is not None:
        return status
    return "transient" if _crash(returncode, text) else "unknown"


def crash_reason(returncode: int, stdout: str, stderr: str) -> str | None:
    """The reason for a crashed process, or None if the failure is not a crash (or a table row
    matches). Never the banner or the prompt: only the signal name or the panic line."""
    text = failure_text(stdout, stderr)
    if _table_status(text) is not None:
        return None
    return _crash(returncode, redact(text))  # redact before the panic line is picked


def _one_line(text: str) -> str:
    """One redacted line, cut to REASON_MAX (redacted first, so a cut leaks no part of a secret)."""
    line = " ".join(redact(text).split())
    return line if len(line) <= REASON_MAX else line[:REASON_MAX - 3] + "..."


def failure_reason(stdout: str, stderr: str) -> str:
    """One line for the comment. Never the banner or the prompt: the turn.failed message
    (the prompt is not echoed with --json), else the matching or last line of stderr."""
    message = _turn_failed(stdout)
    if message is not None:
        return _one_line(message) or "codex failed without a message"
    # redact the whole stderr before a line is picked: a multi-line secret spans lines
    lines = [line for line in redact(stderr).splitlines() if line.strip()]
    for line in lines:
        if any(p.search(line) for _, p in FAILURE_PATTERNS):
            return _one_line(line)
    return _one_line(lines[-1]) if lines else "codex failed without a message"


def _duration(seconds: int) -> str:
    return f"{seconds // 60} min" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} s"


def run_codex(prompt: str, *, schema: Path, sandbox_args: list[str], model: str, effort: str,
              timeout_s: int, cwd: Path) -> CodexRun:
    tmp = Path(tempfile.mkdtemp(prefix="codex-"))
    out = tmp / "last.json"  # fresh path per run: failed runs leave an old file in place (S2 Q1)
    cmd = ["codex", "exec", *BASE_FLAGS, "-m", model, "-c", f'model_reasoning_effort="{effort}"',
           *sandbox_args, "-C", str(cwd), "--output-schema", str(schema), "-o", str(out), "-"]
    try:
        try:
            p = start(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True)
        except FileNotFoundError:
            return CodexRun("unavailable", None, "codex is not installed")
        try:
            stdout, stderr = p.communicate(prompt, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(p)
            return CodexRun("timeout", None, f"no result after {_duration(timeout_s)}")
        except BaseException:  # an interrupt (see catch_signals): no child outlives the caller
            _kill_group(p)
            raise
        finally:
            _children.discard(p)
        if p.returncode != 0:
            text = failure_text(stdout, stderr)
            reason = crash_reason(p.returncode, stdout, stderr) or failure_reason(stdout, stderr)
            return CodexRun(classify_failure(p.returncode, text), None, reason)
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return CodexRun("invalid_output", None, f"output is not JSON: {_one_line(str(e))}")
        if not isinstance(data, dict):
            return CodexRun("invalid_output", None, "output is not a JSON object")
        return CodexRun("ok", data, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _kill_group(p: subprocess.Popen) -> None:
    """SIGKILL the whole process group of `p` (it was started in a new session), then reap it."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()
    _children.discard(p)
    for stream in (p.stdin, p.stdout, p.stderr):
        if stream:
            try:
                stream.close()
            except OSError:
                pass


# --- interrupts --------------------------------------------------------------------------
#
# On SIGINT, SIGTERM or SIGHUP the caller must kill the process group of its running child,
# remove what it created and exit with 128 + the signal number. catch_signals() turns the first
# signal into an Interrupted exception, so `finally` blocks run (Python's default SIGTERM and
# SIGHUP handling skips them). Later signals are ignored, so they cannot stop that cleanup.
# start() registers each child it starts; kill_children() kills what is still registered.
# Inside deferred() (the cleanup itself) a first signal is only recorded; it is raised when the
# block ends, so the cleanup finishes.

INTERRUPT_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Interrupted(BaseException):
    """Raised by the signal handler of catch_signals(). BaseException: no `except Exception` eats it."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


_children: set[subprocess.Popen] = set()
_signum: int | None = None  # the first signal since catch_signals()
_starting = False  # start() is between Popen and the registration of the child
_deferring = False  # inside deferred(): a first signal is raised when the block ends


def _on_signal(signum: int, frame) -> None:
    global _signum
    if _signum is not None:
        return  # a second signal: the cleanup of the first one goes on
    _signum = signum
    if not _starting and not _deferring:
        raise Interrupted(signum)
    # else start() raises once the child is registered, so kill_children() can find it,
    # or deferred() raises once the cleanup is done


def catch_signals() -> dict:
    """Install the interrupt handler. Returns the old handlers for restore_signals()."""
    global _signum
    _signum = None
    return {s: signal.signal(s, _on_signal) for s in INTERRUPT_SIGNALS}


@contextmanager
def deferred():
    """Run a cleanup block that a signal cannot stop. A first signal that arrives inside the block
    is raised as Interrupted when the block ends; a signal from before the block is not raised again."""
    global _deferring
    outer, first = _deferring, _signum is None
    _deferring = True
    try:
        yield
    finally:
        _deferring = outer
        if first and not outer and _signum is not None:
            raise Interrupted(_signum)  # noqa: B012 - the signal came during the cleanup


def restore_signals(old: dict) -> None:
    for s, handler in old.items():
        signal.signal(s, handler)


def start(cmd: list[str], **kwargs) -> subprocess.Popen:
    """Popen in a new session (its own process group), registered for kill_children()."""
    global _starting
    _starting = True
    try:
        p = subprocess.Popen(cmd, start_new_session=True, **kwargs)
        _children.add(p)
        return p
    finally:
        _starting = False
        if _signum is not None:
            raise Interrupted(_signum)  # noqa: B012 - the signal came while the child started


def kill_children() -> None:
    """Kill the process group of every registered child that is still there, and reap it."""
    for p in list(_children):
        _kill_group(p)
