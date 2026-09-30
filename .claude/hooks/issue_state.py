"""Issue state and checks G1-G7 (spec 5.3-5.5).

Pure functions over the facts of one issue. No I/O: the guard (Task 5)
reads the facts with `gh` and `git` and passes them in. Stdlib only.

Comment order is the order of the `gh` comments array (plan decision).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# role -> result markers (spec 5.5)
MARKERS: dict[str, tuple[str, ...]] = {
    "pm": ("## PM: GROOMED", "## PM: NEEDS OWNER", "## PM: WAITING"),
    "engineer": ("## Engineer: DONE", "## Engineer: BLOCKED"),
    "qa": ("## QA: PASS", "## QA: FAIL", "## QA: UNAVAILABLE", "## QA: INVALID", "## QA: UNVERIFIABLE"),
}
RESUME = "## Owner: RESUME"
AGENT_LANE = {"default": "software-engineer", "frontend": "frontend-engineer"}

GROOMED, NEEDS_OWNER, WAITING = MARKERS["pm"]
DONE, BLOCKED = MARKERS["engineer"]
PASS, FAIL, UNAVAILABLE, INVALID, UNVERIFIABLE = MARKERS["qa"]
# A return sends the issue back (spec 5.4 G7): FAIL and BLOCKED, and UNVERIFIABLE (a limit of the
# checker's environment, back to the PM, spec 7). INVALID is not a return: it escalates.
RETURNS = (FAIL, UNVERIFIABLE, BLOCKED)
STOP_RESULTS = (NEEDS_OWNER, INVALID)
MAX_RETURNS = 3

LAUNCH = re.compile(r"^## Launch: (pm|engineer|qa) \((?:attempt|continued, round) (\d+)\)$")
# A receipt that Claude Code denied before the launch ran (PermissionDenied hook, spec 5.3)
NOT_STARTED = re.compile(r"^## Launch not started: ((?:pm|engineer|qa) \((?:attempt|continued, round) \d+\))$")
# A receipt whose agent started, was stopped by an auto mode outage and ended without a result
# (SubagentStop hook, spec 5.3). It voids its receipt exactly like a not-started comment.
STOPPED = re.compile(r"^## Launch stopped by outage: ((?:pm|engineer|qa) \((?:attempt|continued, round) \d+\))$")
# First-line prefixes of a denial without a classifier verdict (Claude Code 2.1.284, spec 5.3)
NO_VERDICT_REASONS = ("Classifier unavailable",
                      "Auto mode could not evaluate this action and is blocking it for safety",
                      "Auto mode unavailable")
_RECEIPT_KEY = re.compile(r"^## Launch: ((?:pm|engineer|qa) \((?:attempt|continued, round) \d+\))$")
_CALL = re.compile(r"^Call: (\S+)$")
CALL_HASH_LEN = 12
REASON_MAX = 200
_LANE = re.compile(r"^Lane: (\S+)$")
_VERIFIED = re.compile(r"^Verified: (\S+)$")
_COMMITS = re.compile(r"^Commits: (\S+?)\.\.(\S+)$")
_WAITING_ON = re.compile(r"^Waiting on: #([1-9][0-9]*)$")


@dataclass(frozen=True)
class Issue:
    number: int
    open: bool
    labels: frozenset[str]
    body: str
    comments: tuple[str, ...]  # comment bodies, oldest first


@dataclass(frozen=True)
class Facts:
    issue: Issue
    head: str  # full SHA of HEAD
    clean: bool  # git status --porcelain is empty
    blocker_open: bool | None = None  # state of the Waiting on: issue, read only when blocker_to_read asks


@dataclass(frozen=True)
class Call:
    role: str  # "pm" | "engineer" | "qa" | "close"
    agent: str  # subagent type, "qa-codex", "" for close; the SendMessage target for a continuation
    issue: int
    continued: bool = False  # a SendMessage continuation (spec 5.3)


# --- parsing -------------------------------------------------------------------


def _comment_bodies(data: dict) -> tuple[str, ...]:
    """The comment bodies, or ValueError when the comment data is missing or malformed."""
    comments = data.get("comments")
    if not isinstance(comments, list):
        raise ValueError(f"issue JSON has no comments list (got {type(comments).__name__})")
    bodies = []
    for n, c in enumerate(comments):
        if not isinstance(c, dict) or not isinstance(c.get("body"), str):
            raise ValueError(f"issue JSON comment {n} has no string body")
        bodies.append(c["body"])
    return tuple(bodies)


def parse_issue(data: dict) -> Issue:
    """Build an Issue from `gh issue view N --json number,state,labels,body,comments`.

    Raises ValueError when `comments` is missing, not a list, or has an element
    without a string `body`: missing comment data must not look like an issue
    without comments.
    """
    return Issue(
        number=int(data["number"]),
        open=data["state"] == "OPEN",
        labels=frozenset(label["name"] for label in data.get("labels") or ()),
        body=data.get("body") or "",
        comments=_comment_bodies(data),
    )


def first_line(body: str) -> str:
    return body.split("\n", 1)[0].rstrip()


def evidence_line(reason: str) -> str:
    """The first line of a reason as is (trailing whitespace kept), cut to REASON_MAX characters."""
    return reason.split("\n", 1)[0][:REASON_MAX]


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _unfenced_lines(text: str):
    """Lines of one text outside fenced code blocks (the fence lines count as inside).

    A fence opens on a line of 3+ backticks or 3+ tildes (at most 3 leading spaces,
    any info string after it) and closes on a line of the same character, at least
    as long, with only spaces or tabs after it. An unclosed fence runs to the end.
    """
    fence = None  # (character, length) of the open fence
    for line in text.split("\n"):
        bare = line.rstrip("\r")
        if fence is None:
            m = _FENCE_OPEN.match(bare)
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
            else:
                yield line
            continue
        char, length = fence
        close = re.match(r"^ {0,3}(" + re.escape(char) + r"{" + str(length) + r",})[ \t]*$", bare)
        if close:
            fence = None


def _value(text: str, pattern: re.Pattern) -> tuple[str, ...] | None:
    """The groups of the lines of `text` outside fences (CR and trailing spaces
    removed) that match `pattern`. None when no line matches or when matching
    lines disagree (an unknown value behaves like a missing one)."""
    found = {m.groups() for line in _unfenced_lines(text) if (m := pattern.match(line.rstrip()))}
    return found.pop() if len(found) == 1 else None


def lane(issue: Issue) -> str | None:
    m = _value(issue.body, _LANE)
    return m[0] if m else None


def verified_sha(body: str) -> str | None:
    m = _value(body, _VERIFIED)
    return m[0] if m else None


def commits_range(body: str) -> tuple[str, str] | None:
    m = _value(body, _COMMITS)
    return (m[0], m[1]) if m else None


def waiting_on(body: str) -> int | None:
    """The issue number of the Waiting on: line, or None when it is missing or lines disagree."""
    m = _value(body, _WAITING_ON)
    return int(m[0]) if m else None


# --- validity, pending, current result (spec 5.3) --------------------------------


def call_hash(tool_use_id: str) -> str:
    """The first 12 hex characters of the SHA-256 of a tool_use_id. The raw id is never posted."""
    return hashlib.sha256(tool_use_id.encode("utf-8")).hexdigest()[:CALL_HASH_LEN]


def _call_value(body: str) -> str | None:
    m = _value(body, _CALL)
    return m[0] if m else None


def is_no_verdict(reason) -> bool:
    """True when the first line of a denial reason starts with one of the no-verdict texts."""
    return isinstance(reason, str) and first_line(reason).startswith(NO_VERDICT_REASONS)


def _not_started(issue: Issue) -> set[int]:
    """Indexes of the voided receipts: a later not-started or stopped-by-outage comment has the
    same `<role> (…)` part and the same Call: value. A receipt without a Call: line is never voided."""
    marks = []  # (index, key, call) of the not-started and stop comments
    for j, body in enumerate(issue.comments):
        line = first_line(body)
        m = NOT_STARTED.match(line) or STOPPED.match(line)
        if m and (value := _call_value(body)):
            marks.append((j, m.group(1), value))
    voided = set()
    for i, body in enumerate(issue.comments):
        m = _RECEIPT_KEY.match(first_line(body))
        if m and (value := _call_value(body)) and any(
                j > i and key == m.group(1) and call == value for j, key, call in marks):
            voided.add(i)
    return voided


def _raw_lines(issue: Issue) -> list[str]:
    return [first_line(c) for c in issue.comments]


def _lines(issue: Issue) -> list[str]:
    """First lines of the comments. A receipt that is not started counts for nothing here:
    its line is blank. Only `attempt` reads the raw lines."""
    voided = _not_started(issue)
    return ["" if i in voided else line for i, line in enumerate(_raw_lines(issue))]


def _result_role(line: str) -> str | None:
    return next((role for role, markers in MARKERS.items() if line in markers), None)


def _launch_role(line: str) -> str | None:
    m = LAUNCH.match(line)
    return m.group(1) if m else None


def _newest_launch(lines: list[str], role: str | None = None) -> int | None:
    idx = [i for i, line in enumerate(lines)
           if (r := _launch_role(line)) and (role is None or r == role)]
    return idx[-1] if idx else None


def _valid(lines: list[str], i: int) -> bool:
    role = _result_role(lines[i])
    j = _newest_launch(lines, role) if role else None
    return j is not None and i > j


def is_pending(issue: Issue) -> bool:
    lines = _lines(issue)
    j = _newest_launch(lines)
    if j is None:
        return False
    role = _launch_role(lines[j])
    return not any(line == RESUME or _result_role(line) == role for line in lines[j + 1:])


def current_result(issue: Issue) -> tuple[int, str] | None:
    lines = _lines(issue)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] == RESUME or _valid(lines, i):
            return i, lines[i]
    return None


def returns_since_resume(issue: Issue) -> int:
    lines = _lines(issue)
    resumes = [i for i, line in enumerate(lines) if line == RESUME]
    start = resumes[-1] + 1 if resumes else 0
    count = 0
    for i in range(start, len(lines)):
        if lines[i] in RETURNS:
            role = _result_role(lines[i])
            if any(_launch_role(line) == role for line in lines[:i]):
                count += 1
    return count


def newest_done(issue: Issue) -> str | None:
    lines = _lines(issue)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] == DONE and _valid(lines, i):
            return issue.comments[i]
    return None


def attempt(issue: Issue, role: str) -> int:
    """A receipt that is not started still counts here, so the next launch gets the next number."""
    return sum(1 for line in _raw_lines(issue) if _launch_role(line) == role) + 1


def launch_comment(call: Call, attempt: int, call_hash: str | None = None) -> str:
    kind = f"continued, round {attempt}" if call.continued else f"attempt {attempt}"
    text = f"## Launch: {call.role} ({kind})\nAgent: {call.agent}"
    return f"{text}\nCall: {call_hash}" if call_hash else text


def _void_comment(issue: Issue, head: str, role: str | None, reason: str,
                  call_hash: str | None = None, verbatim: bool = False) -> str | None:
    """`head` for the newest receipt, or None when the newest receipt has no Call: line (or not one
    equal to call_hash), has another role than `role`, has a result of its role or a RESUME after
    it, or is already voided."""
    raw = _raw_lines(issue)
    j = _newest_launch(raw)
    if j is None or j in _not_started(issue):
        return None
    value = _call_value(issue.comments[j])
    if value is None or (call_hash is not None and value != call_hash):
        return None
    receipt_role = _launch_role(raw[j])
    if role is not None and receipt_role != role:
        return None
    if any(line == RESUME or _result_role(line) == receipt_role for line in raw[j + 1:]):
        return None
    key = raw[j][len("## Launch: "):]
    text = evidence_line(reason) if verbatim else first_line(reason)[:REASON_MAX]
    return f"{head} {key}\nCall: {value}\nReason: {text}"


def not_started_comment(issue: Issue, call_hash: str, reason: str, role: str | None = None) -> str | None:
    """The not-started comment for the newest receipt, or None when it must not be posted:
    the newest receipt has no Call: line equal to call_hash (or another role than `role`),
    has a result of its role or a RESUME after it, or is already not started or stopped."""
    return _void_comment(issue, "## Launch not started:", role, reason, call_hash)


def outage_stop_comment(issue: Issue, role: str, reason: str) -> str | None:
    """The stop comment for the newest receipt of an agent that an auto mode outage stopped, or None:
    the newest receipt has no Call: line, has another role than `role`, has a result of its role or
    a RESUME after it, or is already not started or stopped. The reason (the evidence content)
    is posted as is: its first line, trailing whitespace kept, cut to REASON_MAX characters."""
    return _void_comment(issue, "## Launch stopped by outage:", role, reason, verbatim=True)


def _two_not_started(issue: Issue) -> bool:
    """The two newest receipts are both voided (not started or stopped by an outage, any mix),
    and no result and no RESUME follows the older one."""
    raw = _raw_lines(issue)
    receipts = [i for i, line in enumerate(raw) if _launch_role(line)]
    if len(receipts) < 2 or not set(receipts[-2:]) <= _not_started(issue):
        return False
    return not any(line == RESUME or _result_role(line) for line in raw[receipts[-2] + 1:])


# --- checks (spec 5.4) -----------------------------------------------------------


def _current(issue: Issue) -> tuple[int | None, str | None, str]:
    """(index, marker, text for messages) of the current result."""
    cur = current_result(issue)
    if cur is None:
        return None, None, "none"
    return cur[0], cur[1], cur[1]


def g1(call: Call, facts: Facts) -> str | None:
    iss = facts.issue
    if iss.number != call.issue:
        return f"G1: facts are for issue #{iss.number}, expected issue #{call.issue}"
    if not iss.open:
        return f"G1: issue #{iss.number} is closed, expected an open issue"
    if "waiting" in iss.labels:  # before the ready check: a waiting issue waits, also with ready
        return (f"G1: issue #{iss.number} has the label waiting, expected no label waiting "
                f"(the orchestrator adds ready again when the blocker is closed)")
    if "ready" not in iss.labels:
        return f"G1: issue #{iss.number} has no label ready, expected the label ready"
    for label in ("later", "needs-owner"):
        if label in iss.labels:
            return f"G1: issue #{iss.number} has the label {label}, expected no label later or needs-owner"
    if not facts.clean:
        return "G1: working tree is not clean, expected a clean tree (git status --porcelain empty)"
    if call.role != "close" and _two_not_started(iss):
        return (f"G1: issue #{iss.number}: the last 2 launches did not start or were stopped by an auto mode "
                f"outage, expected a launch that runs; stop the loop, the owner posts {RESUME}")
    if is_pending(iss):
        lines = _lines(iss)
        j = _newest_launch(lines)
        return (f"G1: issue #{iss.number} is pending: {lines[j]} has no result, "
                f"expected a result of {_launch_role(lines[j])} or {RESUME}")
    return None


def _waiting_blocker(facts: Facts) -> tuple[int | None, str | None]:
    """(blocker, None) for a current WAITING result with a usable Waiting on: line, else (None, G2 reason)."""
    i, _, _ = _current(facts.issue)
    body = facts.issue.comments[i]
    n = waiting_on(body)
    if n is None:
        count = len({line.rstrip() for line in _unfenced_lines(body) if line.startswith("Waiting on:")})
        what = "no Waiting on: line" if count == 0 else "Waiting on: lines that disagree or are not #<N>"
        return None, f"G2: current result is {WAITING} with {what}, expected exactly one line Waiting on: #<N>"
    if n == facts.issue.number:
        return None, (f"G2: current result is {WAITING} on #{n}, the issue itself, "
                      f"expected Waiting on: another issue")
    return n, None


def blocker_to_read(call: Call, facts: Facts) -> int | None:
    """The issue whose state the guard must read and pass in as facts.blocker_open: only for a PM
    call whose current result is WAITING with a usable Waiting on: line, and only when G1 allows it."""
    if call.role != "pm" or _current(facts.issue)[1] != WAITING or g1(call, facts) is not None:
        return None
    return _waiting_blocker(facts)[0]


def g2(call: Call, facts: Facts) -> str | None:
    lines = _lines(facts.issue)
    _, marker, found = _current(facts.issue)
    if _newest_launch(lines) is None or marker in (BLOCKED, UNVERIFIABLE, RESUME):
        return None
    if marker == WAITING:
        n, reason = _waiting_blocker(facts)
        if reason:
            return reason
        if facts.blocker_open is None:
            return f"G2: current result is {WAITING} on #{n}, but the state of #{n} was not read"
        if facts.blocker_open:
            return f"G2: current result is {WAITING} on #{n}, and #{n} is open, expected #{n} closed"
        return None
    return (f"G2: current result is {found}, expected no launch comment yet, "
            f"{BLOCKED}, {UNVERIFIABLE}, {RESUME} or {WAITING} with a closed blocker")


def g3(call: Call, facts: Facts) -> str | None:
    _, marker, found = _current(facts.issue)
    if marker not in (GROOMED, FAIL):
        return f"G3: current result is {found}, expected {GROOMED} or {FAIL}"
    if call.continued:
        return None  # the SendMessage target is a name, not a type (spec 5.3)
    value = lane(facts.issue)
    if value is None:
        return f"G3: issue body has no Lane line, expected Lane: {' or Lane: '.join(AGENT_LANE)}"
    if value not in AGENT_LANE:
        return f"G3: lane is {value}, expected {' or '.join(AGENT_LANE)}"
    if call.agent != AGENT_LANE[value]:
        return f"G3: agent is {call.agent or 'none'}, expected {AGENT_LANE[value]} for lane {value}"
    return None


def g4(call: Call, facts: Facts) -> str | None:
    i, marker, found = _current(facts.issue)
    if marker == PASS:
        sha = verified_sha(facts.issue.comments[i])
        if sha == facts.head:
            return (f"G4: current result is {PASS} with verified SHA {sha} equal to HEAD, "
                    f"expected {DONE} or a {PASS} with a verified SHA not equal to HEAD")
    elif marker != DONE:
        return (f"G4: current result is {found}, "
                f"expected {DONE} or a {PASS} with a verified SHA not equal to HEAD")
    done = newest_done(facts.issue)
    if done is None:
        return f"G4: no valid {DONE} comment found, expected one with a Commits: line"
    if commits_range(done) is None:
        return f"G4: the newest {DONE} comment has no Commits: line, expected Commits: <base>..<head>"
    return None


def g5(call: Call, facts: Facts) -> str | None:
    _, marker, found = _current(facts.issue)
    if marker == UNAVAILABLE:
        return None
    return f"G5: current result is {found}, expected {UNAVAILABLE}"


def g6(call: Call, facts: Facts) -> str | None:
    i, marker, found = _current(facts.issue)
    if marker != PASS:
        return f"G6: current result is {found}, expected {PASS} with a verified SHA equal to HEAD"
    sha = verified_sha(facts.issue.comments[i])
    if sha != facts.head:
        return f"G6: verified SHA is {sha or 'missing'}, expected HEAD {facts.head}"
    return None


def g7(call: Call, facts: Facts) -> str | None:
    n = returns_since_resume(facts.issue)
    if n < MAX_RETURNS:
        return None
    return (f"G7: {n} returns ({FAIL}, {UNVERIFIABLE} or {BLOCKED}) since the newest {RESUME}, "
            f"expected fewer than {MAX_RETURNS}")


def _role_checks(call: Call):
    """The role checks for a call, or a G1 deny message if the call cannot be placed."""
    if call.role == "pm":
        if not call.continued and call.agent != "pm":
            return f"G1: pm call with agent {call.agent or 'none'}, expected agent pm"
        return (g2, g7)
    if call.role == "engineer":
        return (g3, g7)
    if call.role == "qa":
        if call.continued or call.agent == "qa-engineer":
            return (g5,)  # only the qa-engineer subagent can be continued (spec 5.1, 5.3)
        if call.agent == "qa-codex":
            return (g4,)
        return f"G1: qa call with agent {call.agent or 'none'}, expected qa-codex or qa-engineer"
    if call.role == "close":
        return (g6,)
    return f"G1: unknown role {call.role or 'none'}, expected pm, engineer, qa or close"


def check(call: Call, facts: Facts) -> str | None:
    """None = allow, else the deny message. Never allows a call it cannot place."""
    checks = _role_checks(call)
    if isinstance(checks, str):
        return checks
    for g in (g1, *checks):
        reason = g(call, facts)
        if reason is not None:
            return reason
    return None
