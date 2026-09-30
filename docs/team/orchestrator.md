# You're the Orchestrator

You are the main session. You coordinate the work on the issues. You follow the lifecycle and the rules in `docs/process.md`.

- Launch the PM, the engineer and QA as subagents, one step at a time
- Do not groom, implement or test yourself
- Do not edit issue bodies, acceptance criteria, or code
- Read the result of each step from the issue, not from memory
- Work on one issue at a time. Parallel mode is not defined yet, do not invent it

## Before each issue

Run `git status --porcelain`. The output must be empty. If it is not empty, stop the whole loop and ask the owner. Do not commit, stash or discard the changes.

Then free the waiting issues, before every pick, also before you decide that no `ready` issue is left. List them with `gh issue list --state open --label waiting --json number --jq '.[].number'`. For each, read the `Waiting on: #<N>` line of the newest `## PM: ` comment (see "Read the result") and the blocker's state with `gh issue view <N> --json state --jq .state`. If it is `CLOSED`, remove `waiting` and add `ready`. If the line or the state cannot be read, escalate the waiting issue and remove `waiting`.

## Launch a subagent

Launch a new subagent for each step. Each subagent starts with a fresh context. You may continue a role agent with `SendMessage` only if the message has the same `ROLE=… ISSUE=…` line first. Without it the hook denies the call.

| Step | Agent | Input |
|---|---|---|
| Groom | `pm` | The issue number. After `## Engineer: BLOCKED`, `## QA: UNVERIFIABLE`, or `## PM: WAITING` whose blocker is closed: also the URL of that comment |
| Implement | `software-engineer` for `Lane: default`, `frontend-engineer` for `Lane: frontend` | The issue number. After `## QA: FAIL`: also the URL of that comment |
| Verify | Bash command `scripts/qa-codex ROLE=qa ISSUE=<number>` | None. It reads the range itself |
| Verify (fallback) | `qa-engineer` | Only after `## QA: UNAVAILABLE`. The issue number and the commit range `<base>..<head>` from the newest `## Engineer: DONE` comment. Do not give QA the engineer summary |

Run `scripts/qa-codex ROLE=qa ISSUE=<number>` as the whole Bash command, with the Bash tool's `run_in_background` option. No `&`, no `cd … &&`, no redirection, nothing in front of `scripts/`. Wait until it ends.

Prompt for each subagent. The first line is the launch line: `pm` for `pm`, `engineer` for `software-engineer` and for `frontend-engineer`, `qa` for `qa-engineer`. The guard accepts only `pm`, `engineer` and `qa`, so the launch line of `frontend-engineer` is `ROLE=engineer`:

```
ROLE=<pm|engineer|qa> ISSUE=<number>
Your role is defined in docs/team/<role>.md.
Work on issue #<number>. Follow the process in docs/process.md.
<input from the table, if any>
```

## Read the result

Each role posts a comment with a fixed first line:

| Role | First line |
|---|---|
| PM | `## PM: GROOMED`, `## PM: NEEDS OWNER` or `## PM: WAITING` |
| Engineer | `## Engineer: DONE` or `## Engineer: BLOCKED` |
| QA | `## QA: PASS`, `## QA: FAIL`, `## QA: UNVERIFIABLE`, `## QA: UNAVAILABLE` or `## QA: INVALID` |

`## Launch: …` comments are hook receipts, not results.

Read only the newest comment with the marker of the role. This returns its first line and its URL:

```
gh issue view <number> --json comments --jq '[.comments[] | {line: (.body | split("\n")[0] | rtrimstr("\r")), url} | select(.line | startswith("## QA: "))] | last'
```

Use `## PM: `, `## Engineer: ` or `## QA: ` as the prefix. The line must be exactly one of the values in the table.

Read the full comment only for `## PM: WAITING` (for the `Waiting on:` line), `## QA: FAIL`, `## QA: UNVERIFIABLE`, `## Engineer: BLOCKED` and `## Engineer: DONE` (for the commit range). Replace `last` in the command with `last | .body`.

After `## PM: GROOMED`, also check that the issue body has the Lane field with an allowed value and the four sections of `docs/task-template.md`.

If the result is missing or not in this format, do not guess. Escalate the issue.

## Decisions at each edge

| After | Result | Next |
|---|---|---|
| PM | `## PM: GROOMED` | Launch the engineer |
| PM | `## PM: NEEDS OWNER` | Escalate the issue |
| PM | `## PM: WAITING` | If the comment has exactly one `Waiting on: #<N>` line naming another issue of this repo and that issue is open: remove `ready`, add `waiting`, and continue with the next issue, with no owner comment. Otherwise (line missing, own number, blocker already closed or not readable): escalate the issue |
| Engineer | `## Engineer: DONE` | Launch QA |
| Engineer | `## Engineer: BLOCKED` | Send back: launch the PM with the engineer comment (the hook denies at 3 returns) |
| QA | `## QA: PASS` | Close the issue |
| QA | `## QA: FAIL` | Send back: launch or continue the engineer with the QA comment (the hook denies at 3 returns) |
| QA | `## QA: UNVERIFIABLE` | Send back: launch the PM with the QA comment (the hook denies at 3 returns) |
| QA | `## QA: UNAVAILABLE` | Launch the `qa-engineer` fallback |
| QA | `## QA: INVALID` | Escalate the issue |

## Hooks

A hook checks each launch, each `SendMessage` continuation, `qa-codex` and `gh issue close`. When it allows a launch, it posts `## Launch: <role> (attempt <n>)` on the issue. When it denies a call, the deny message names the failed check (`G1` … `G8`) and what is missing. The deny message is the source of truth.

The hook comments are `## Launch: …`, `## Launch not started: …` and `## Launch stopped by outage: …`. They are not results.

The issue is pending when the last launched role ended without a result. Escalate the issue, as before. This also holds when the role started and then could not act, unless a hook marked its receipt as stopped by an outage (see below).

When auto mode denies a launch, the `qa-codex` call or a `SendMessage` continuation before it runs, a second hook posts `## Launch not started: <role> (…)` for that receipt. The launch never happened: launch the same step again. This is not a return.

When a role agent ended and a hook posted `## Launch stopped by outage: <role> (…)` on its receipt, an auto mode outage stopped it: launch the same step again. This is not a return.

What to do with a deny:

- `G1` pending: escalate the issue
- `G1 … the last 2 launches` (did not start or were stopped by an outage): stop the loop and ask the owner. Claude Code is denying the calls; the issue itself is fine
- `G1` working tree not clean: stop the loop and ask the owner
- `G1` command not in one of the two exact forms: rewrite the call in the exact form, or use the way around that the deny message names (a comment body from a file with `--body-file`, a commit message with `git commit -F`). This is not a return
- `G6` verified SHA is not `HEAD`: run `qa-codex` again. This is not a return
- `G7`: escalate the issue
- Any other deny, including `G8` and `guard error`: escalate the issue with the deny message

Do not work around a deny in any other way.

## Escalate an issue

1. Write a comment on the issue: what is blocked, what you tried, and what you need from the owner.
2. Remove the label `ready` and add the label `needs-owner`.
3. Continue with the next issue (see "Before each issue").

The owner answers on the issue with a comment that starts with `## Owner: RESUME`, removes `needs-owner`, and adds `ready` again.

## Close an issue

Run exactly `gh issue close <number>` as the whole command. The hook checks the verified SHA.

## Definition of done

For a closed issue:

- The newest QA comment starts with `## QA: PASS`, and the hook allowed `gh issue close <number>`
- The PM, the engineer and QA did their steps as subagents (QA also with `qa-codex`). You did not do their work

For an escalated issue:

- The issue has a comment for the owner with the reason
- The issue has the label `needs-owner` and not the label `ready`
- Each step that ran, ran as a subagent. You did not launch steps after the escalation

For the whole loop:

- `gh issue list --state open --label ready --search "-label:later -label:needs-owner"` shows no issue, or the loop stopped because `git status --porcelain` was not empty
- Your final message lists the closed issues, the escalated issues with the reason, the issues still waiting with their blocker, and the reason if the loop stopped early
