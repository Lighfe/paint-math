# Process

This document tells how work is organized in this repo.

Status: hooks in `.claude/hooks/` check each launch of a role and each `gh issue close`, and deny the call when the issue is not in the right state. This prose stays the main description. There are no Jev gates yet.

## Work rules

- Tasks are GitHub issues, one at a time
- Read the acceptance criteria before starting and before closing
- Commit regularly
- Do not read old reviews in `docs/reviews/` unless the owner points to one.

## Roles

- Orchestrator - the main session, follows `docs/team/orchestrator.md`
- PM - grooms a task before anyone implements it, follows `docs/team/pm.md`
- Engineer - implements one groomed task, follows `docs/team/software-engineer.md`
- QA - checks the result against the acceptance criteria, follows `docs/team/qa-engineer.md`

A groomed issue uses the template in `docs/task-template.md`.

## Intake

- A superpowers plan in `docs/plans/` is turned into GitHub issues.
- Each plan task becomes one issue in `docs/task-template.md` format.
- The owner adds the label `ready` to the issues that the loop may work on.
- Issues with the label `later` are out of scope for the current implementation. Do not work on them.

## Lifecycle

1. Pick the next open issue with the label `ready`
2. PM grooms it
3. Engineer implements it
4. If the engineer reports a blocked criterion, back to step 2 with the engineer comment as input
5. QA verifies it
6. On FAIL, back to step 3 with the QA comment as input
7. On `## QA: UNVERIFIABLE`, back to step 2 (PM) with the QA comment as input
8. On PASS, close the issue
9. Repeat until no open issue has the label `ready`

Stop condition for `/goal`: no open issue has the label `ready`.

## Rules

- Do not skip step 2
- The engineer does not close the issue
- QA does not fix the code, only outputs PASS or FAIL
- The orchestrator closes the issue only after QA outputs PASS, and only if the SHA that QA verified is the current `HEAD`
- A return is a QA FAIL, a QA UNVERIFIABLE or an engineer BLOCKED. After 3 returns on the same issue, escalate the issue. The count starts after the newest `## Owner: RESUME` comment
- A launch that Claude Code denied before it ran (the hook posts `## Launch not started: …`) is not pending and not a return
- If the PM cannot resolve a blocked criterion, escalate the issue
- `## QA: UNVERIFIABLE` means QA could not check a criterion because of a tool or sandbox limit of the checker. The PM makes the criterion checkable with the same intent. The PM escalates (`## PM: NEEDS OWNER`) when making a criterion checkable changes its intent or scope, or needs an edit of the project settings files (`.claude/settings*.json`), `.claude/hooks/` or the QA sandbox
- `## QA: INVALID` has other causes (for example no usable commit range, or retries used up) and is escalated
- Before the next issue, the working tree must be clean (`git status --porcelain` is empty). If not, stop the whole loop and ask the owner

## Escalation

- The orchestrator comments the reason, removes the label `ready`, and adds the label `needs-owner`. Then it continues with the next issue.
- The owner answers with a comment that starts with `## Owner: RESUME`, removes `needs-owner`, and adds `ready` again.
- After `## Owner: RESUME`, the issue goes back to the PM.
