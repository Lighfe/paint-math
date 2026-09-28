#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Codex review (spec 8): `uv run --script .agents/skills/codex-review/review.py --target <path-or-range> --topic <slug>`.

Runs `codex exec` read-only on one file, folder or commit range, with a normal (not adversarial)
review prompt and review.schema.json. On success it writes
`docs/reviews/<YYYY-MM-DD>-<topic>-codex-review.md` (redacted, every finding with `Decision: open`),
prints only its path and exits 0. On a failed run or an output of the wrong shape it writes no file,
prints the reason on stderr and exits 1. Bad arguments exit 2. An interrupt exits 128 + the signal.
Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import re
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_exec  # noqa: E402

SCHEMA = Path(__file__).resolve().parent / "review.schema.json"
REVIEWS_DIR = ROOT / "docs" / "reviews"

MODEL = "gpt-6-astra"  # the same as QA_MODEL in scripts/qa-codex
EFFORT = "medium"
SANDBOX = ["-s", "read-only"]  # spike S2 Q3
TIMEOUT_S = 1800

TOPIC_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
USAGE = "usage: review.py --target <path-or-range> --topic <slug>"

VERDICTS = ("approve", "needs-attention")
SEVERITIES = ("critical", "high", "medium", "low")
TOP_KEYS = ("verdict", "summary", "findings", "next_steps")
FINDING_KEYS = ("severity", "title", "body", "file", "line_start", "line_end", "confidence", "recommendation")


def build_prompt(target: str) -> str:
    return (
        f"Review {target} for correctness, gaps and risks. "
        "For a commit range, run `git diff <range>`. "
        "Report findings in the schema. "
        "Do not quote secrets (keys, tokens, passwords) in your findings; name them instead.\n"
    )


# --- validation and rendering ------------------------------------------------------------


def _check_keys(obj: dict, required: tuple[str, ...], where: str, errors: list[str]) -> None:
    errors += [f"{where}: missing property {k}" for k in required if k not in obj]
    errors += [f"{where}: extra property {k}" for k in sorted(set(obj) - set(required), key=str)]


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate(data) -> list[str]:
    """The rules of review.schema.json. Empty list = valid."""
    if not isinstance(data, dict):
        return ["output is not a JSON object"]
    errors: list[str] = []
    _check_keys(data, TOP_KEYS, "output", errors)
    if "verdict" in data and data["verdict"] not in VERDICTS:
        errors.append(f"verdict is {data['verdict']!r}, expected approve or needs-attention")
    if "summary" in data and not isinstance(data["summary"], str):
        errors.append("summary is not a string")
    steps = data.get("next_steps")
    if "next_steps" in data and (not isinstance(steps, list) or not all(isinstance(s, str) for s in steps)):
        errors.append("next_steps is not an array of strings")
    findings = data.get("findings")
    if "findings" in data and not isinstance(findings, list):
        errors.append("findings is not an array")
    elif isinstance(findings, list):
        for n, f in enumerate(findings):
            where = f"findings[{n}]"
            if not isinstance(f, dict):
                errors.append(f"{where} is not an object")
                continue
            _check_keys(f, FINDING_KEYS, where, errors)
            if "severity" in f and f["severity"] not in SEVERITIES:
                errors.append(f"{where}.severity is {f['severity']!r}, expected one of {', '.join(SEVERITIES)}")
            errors += [f"{where}.{k} is not a string" for k in ("title", "body", "file", "recommendation")
                       if k in f and not isinstance(f[k], str)]
            errors += [f"{where}.{k} is not an integer" for k in ("line_start", "line_end")
                       if k in f and not _is_int(f[k])]
            if "confidence" in f and (isinstance(f["confidence"], bool)
                                      or not isinstance(f["confidence"], (int, float))):
                errors.append(f"{where}.confidence is not a number")
    return errors


def _lines(f: dict) -> str:
    start, end = f["line_start"], f["line_end"]
    return f"{start}" if start == end else f"{start}-{end}"


def render_review(data: dict, *, topic: str, target: str, date: str) -> str:
    out = [f"# Codex review: {topic}", "",
           f"- Date: {date}", f"- Target: {target}", f"- Verdict: {data['verdict']}", "",
           "## Summary", "", data["summary"].strip(), "",
           "## Findings", ""]
    if not data["findings"]:
        out += ["No findings.", ""]
    for i, f in enumerate(data["findings"], 1):
        out += [f"### {i}. [{f['severity']}] {f['title'].strip()}", "",
                f"- File: {f['file']}:{_lines(f)}", f"- Confidence: {f['confidence']}", "",
                f["body"].strip(), "",
                f"Recommendation: {f['recommendation'].strip()}", "",
                "Decision: open", ""]
    out += ["## Next steps", ""]
    out += [f"- {s.strip()}" for s in data["next_steps"]] or ["None."]
    return "\n".join(out) + "\n"


# --- arguments ---------------------------------------------------------------------------


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(message)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = _Parser(prog="review.py", add_help=False)
    parser.add_argument("--target", required=True)
    parser.add_argument("--topic", required=True)
    return parser.parse_args(argv)


def check_target(target: str) -> str | None:
    """None if `target` is an existing path under the repo root or a commit range that
    `git rev-list` accepts, else the reason."""
    if not target or target.startswith("-"):
        return f"target {target!r} is not a path or a commit range"
    path = (ROOT / target).resolve()
    if path.exists():
        if path != ROOT and ROOT not in path.parents:
            return f"target {target} is outside the repo root {ROOT}"
        return None
    p = subprocess.run(["git", "rev-list", "--quiet", "--end-of-options", target, "--"], cwd=ROOT,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return f"target {target} is neither an existing path under {ROOT} nor a commit range"
    return None


# --- main --------------------------------------------------------------------------------


_created: Path | None = None  # the review file this run created (removed after an interrupt)


def _write(path: Path, text: str) -> None:
    global _created
    with codex_exec.deferred():  # a signal during the write is raised after it; main removes the file
        with open(path, "x", encoding="utf-8") as f:
            _created = path
            f.write(text)


def main(argv: list[str], *, today: str | None = None) -> int:
    """One review run. On SIGINT, SIGTERM or SIGHUP: kill the Codex child, write no file and
    return 128 + the signal number."""
    global _created
    _created = None
    old = codex_exec.catch_signals()
    try:
        return _main(argv, today)
    except codex_exec.Interrupted as e:
        with codex_exec.deferred():
            codex_exec.kill_children()
            if _created is not None:
                _created.unlink(missing_ok=True)
        try:  # after SIGHUP the terminal may be gone
            print(f"codex-review: stopped by {signal.Signals(e.signum).name}; no review file written",
                  file=sys.stderr)
        except OSError:
            pass
        return 128 + e.signum
    finally:
        codex_exec.restore_signals(old)


def _main(argv: list[str], today: str | None) -> int:
    try:
        args = _parse(argv)
    except _UsageError as e:
        print(f"{USAGE}\ncodex-review: {e}", file=sys.stderr)
        return 2
    if not TOPIC_RE.fullmatch(args.topic):
        print(f"{USAGE}\ncodex-review: topic {args.topic!r} must match {TOPIC_RE.pattern}", file=sys.stderr)
        return 2
    error = check_target(args.target)
    if error is not None:
        print(f"{USAGE}\ncodex-review: {error}", file=sys.stderr)
        return 2
    date = today or datetime.date.today().isoformat()
    path = REVIEWS_DIR / f"{date}-{args.topic}-codex-review.md"
    if path.exists():
        print(f"codex-review: {path} already exists; choose another topic", file=sys.stderr)
        return 1

    run = codex_exec.run_codex(build_prompt(args.target), schema=SCHEMA, sandbox_args=SANDBOX, model=MODEL,
                               effort=EFFORT, timeout_s=TIMEOUT_S, cwd=ROOT)
    status, reason = run.status, run.reason
    if status == "ok":
        errors = validate(run.output)
        if errors:
            status, reason = "invalid_output", "output does not match the schema: " + "; ".join(errors)
    if status != "ok":
        print(f"codex-review: {status}: {codex_exec._one_line(reason)}", file=sys.stderr)
        return 1

    text = codex_exec.redact(render_review(run.output, topic=args.topic, target=args.target, date=date))
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _write(path, text)
    except FileExistsError:
        print(f"codex-review: {path} already exists; choose another topic", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
